"""The stylesheet contrast audit, and the guard it exists to provide (#306).

The bug this prevents shipped and stayed shipped: the classic arena's sheet
inverted the page's ground and ink but repainted only some of the components
standing on them, so the battle log and every error notice rendered as
near-white text on a near-white panel. Nothing failed, because valid CSS that
renders an unreadable page is still valid CSS.

:class:`EveryRegisteredThemeIsReadableTest` is the guard. The rest of this
module exists so the guard can be trusted: a check nobody has watched fail is
indistinguishable from a check that cannot fail.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

from common.css_contrast import (
    DECORATIVE_COMPONENTS,
    audit_conditionals,
    audit_text_on_ground,
    audit_theme,
    colour_literals,
    conditional_layers,
    contrast_ratio,
    custom_properties,
    failures,
    relative_luminance,
    stylesheet,
    stylesheet_names,
    undefined_custom_properties,
)
from common.theme import THEMES


class ContrastMathTest(SimpleTestCase):
    """The WCAG arithmetic, against values that can be checked by hand."""

    def test_black_on_white_is_the_maximum_ratio(self):
        self.assertAlmostEqual(contrast_ratio("#000000", "#ffffff"), 21.0, places=2)

    def test_a_colour_against_itself_is_the_minimum_ratio(self):
        self.assertAlmostEqual(contrast_ratio("#7a7a7a", "#7a7a7a"), 1.0, places=6)

    def test_the_ratio_does_not_depend_on_which_colour_is_the_text(self):
        self.assertAlmostEqual(
            contrast_ratio("#123456", "#fedcba"),
            contrast_ratio("#fedcba", "#123456"),
            places=9,
        )

    def test_three_digit_shorthand_means_the_same_as_six(self):
        self.assertAlmostEqual(
            relative_luminance("#fff"), relative_luminance("#ffffff"), places=9
        )
        self.assertAlmostEqual(
            relative_luminance("#c00"), relative_luminance("#cc0000"), places=9
        )

    def test_the_failure_that_shipped_measures_as_unreadable(self):
        """#f1eff7 ink on the #f5f5f5 panel .dice-log used to render as."""
        self.assertLess(contrast_ratio("#f1eff7", "#f5f5f5"), 1.1)


class EveryRegisteredThemeIsReadableTest(SimpleTestCase):
    """No registered theme may produce text below WCAG AA. This is the guard.

    It enumerates ``common.theme.THEMES`` rather than naming sheets, so a
    theme added later is covered the day it is registered rather than the day
    somebody remembers to add it here.
    """

    def test_every_registered_theme_meets_wcag_aa(self):
        base = stylesheet("base.css")
        for theme in THEMES.values():
            with self.subTest(theme=theme.key):
                sheet = (
                    stylesheet(Path(theme.stylesheet).name) if theme.stylesheet else ""
                )
                unreadable = failures(base, sheet, theme.key)
                self.assertEqual(
                    unreadable,
                    [],
                    "Unreadable text:\n  "
                    + "\n  ".join(str(pairing) for pairing in unreadable),
                )

    def test_each_theme_actually_has_pairings_to_check(self):
        """Guards the test above: zero failures out of zero pairings is not a pass."""
        base = stylesheet("base.css")
        for theme in THEMES.values():
            with self.subTest(theme=theme.key):
                sheet = (
                    stylesheet(Path(theme.stylesheet).name) if theme.stylesheet else ""
                )
                self.assertGreater(len(audit_theme(base, sheet, theme.key)), 20)


class EverySheetTheSiteLoadsIsReadableTest(SimpleTestCase):
    """The same guard, over the component sheets rather than base.css alone (#730).

    The test above names two sheets. The site loads eighteen, and the fifteen
    component sheets were measured by nobody: eighteen text colours sat below
    AA on their own grounds -- a green at 3.30:1, a gold at 2.42:1, greys on
    tinted panels -- while the guard was green. Naming sheets is what let that
    happen, so this enumerates the directory: a sheet added tomorrow is
    audited the day it lands.

    This calls :func:`failures`, which is every audit the module has: text on
    an inherited ground, text on a component that paints itself, and both of
    those through each conditional block. #730 could only assert the first of
    the three, because the six painted-component pairings the other two found
    were undecided design questions about backgrounds; #732 settled them, so
    the guard is now the whole audit over the whole directory.
    """

    def _every_sheet_but_the_themes(self) -> str:
        """base.css and every component sheet, concatenated as the page loads them."""
        theme_sheets = {
            Path(theme.stylesheet).name for theme in THEMES.values() if theme.stylesheet
        }
        component_sheets = [
            name
            for name in stylesheet_names()
            if name != "base.css" and name not in theme_sheets
        ]
        self.assertGreater(len(component_sheets), 10)
        return "\n".join(
            [stylesheet("base.css")] + [stylesheet(name) for name in component_sheets]
        )

    def test_no_sheet_the_site_loads_puts_any_text_below_aa(self):
        """The whole audit over every sheet -- what #730 asked to be able to run."""
        sheets = self._every_sheet_but_the_themes()
        for theme in THEMES.values():
            with self.subTest(theme=theme.key):
                theme_sheet = (
                    stylesheet(Path(theme.stylesheet).name) if theme.stylesheet else ""
                )
                unreadable = failures(sheets, theme_sheet, theme.key)
                self.assertEqual(
                    unreadable,
                    [],
                    "Unreadable text:\n  "
                    + "\n  ".join(str(pairing) for pairing in unreadable),
                )

    def test_there_are_component_pairings_to_check(self):
        """Guards the test above: zero failures out of zero pairings is not a pass."""
        only_base = audit_text_on_ground(stylesheet("base.css"))
        every_sheet = audit_text_on_ground(self._every_sheet_but_the_themes())

        self.assertGreater(len(every_sheet), len(only_base) + 100)

    def test_the_painted_components_are_checked_too_not_only_the_ground_text(self):
        """The other half of the audit, which #730 could not assert (#732)."""
        painted = audit_theme(self._every_sheet_but_the_themes())

        self.assertGreater(len(painted), 100)

    def test_a_failure_in_a_component_sheet_is_reported_once_not_once_per_block(self):
        """The inflation #732 was filed for: six pairings read as 138.

        An unconditional failure used to come back from every ``@media`` block
        as well as from the sheet, so the count scaled with how many blocks
        the sheets happened to have rather than with how many things were
        wrong. Measured here over the live sheets with one planted failure, so
        it states the ratio of reports to problems rather than a fixed number.
        """
        sheets = self._every_sheet_but_the_themes()
        blocks = len(conditional_layers(sheets))
        self.assertGreater(blocks, 10, "the live sheets need blocks to inflate by")
        planted = sheets + "\n.planted-failure { background: #eeeeee; color: #dddddd; }"

        reported = failures(planted, "", "tarmar")

        self.assertEqual(
            [pairing.selector for pairing in reported], [".planted-failure"]
        )


class TheDarkenedTextColoursClearTheirGroundsTest(SimpleTestCase):
    """What #730 decided, stated as ratios rather than as three hex literals.

    The flat audit reads a ground from the selectors themselves, so it cannot
    see that ``.turn-option-ai-pick`` stands inside a turn option that turns
    gold when it is pressed, or that an equipped item can sit on a dropped
    row's muted ground. Those grounds are named here, and each token is read
    from base.css rather than restated, so a later edit to the palette is
    measured instead of quietly drifting past this.
    """

    def ratio_of_token(self, token: str, ground: str) -> float:
        """Contrast between a base.css colour token and a ground it lands on.

        Args:
            token: Name of the text colour's token in base.css.
            ground: Name of the ground's token, or a literal colour.

        Returns:
            The WCAG ratio between the two, 1.0 to 21.0.
        """
        palette = custom_properties(stylesheet("base.css"))
        self.assertIn(token, palette, f"base.css no longer defines {token}")
        return contrast_ratio(palette[token].strip(), palette.get(ground, ground))

    def test_the_positive_green_reads_on_the_sheet_and_on_a_muted_row(self):
        for ground in ("--colour-surface", "--colour-surface-muted"):
            with self.subTest(ground=ground):
                self.assertGreaterEqual(
                    self.ratio_of_token("--colour-sheet-positive", ground), 4.5
                )

    def test_the_gold_used_as_text_reads_on_every_ground_it_stands_on(self):
        grounds = (
            "--colour-surface",
            "--colour-surface-gold-pressed",  # a pressed turn option
            "--colour-surface-warm",  # the battle panels
        )
        for ground in grounds:
            with self.subTest(ground=ground):
                self.assertGreaterEqual(
                    self.ratio_of_token("--colour-accent-gold-on-light", ground), 4.5
                )

    def test_the_gold_fill_is_left_where_it_is(self):
        """Darkening the fill was not the decision; a separate text token was.

        --colour-accent-gold-strong stays light because it is a fill, a
        border and a focus ring, and it is measured against the near-black
        ink that sits on it rather than against a white page.
        """
        palette = custom_properties(stylesheet("base.css"))

        self.assertEqual(palette["--colour-accent-gold-strong"].strip(), "#a8871f")
        self.assertGreaterEqual(
            self.ratio_of_token(
                "--colour-accent-gold-ink", "--colour-accent-gold-strong"
            ),
            4.5,
        )

    def test_the_negative_red_reads_on_the_tints_it_is_printed_on(self):
        grounds = (
            "--colour-surface",
            "--colour-surface-tint",  # the encumbrance bar's penalty
            "--colour-sheet-negative-surface-faint",  # the death card
            "--colour-sheet-negative-border",  # the remove button
        )
        for ground in grounds:
            with self.subTest(ground=ground):
                self.assertGreaterEqual(
                    self.ratio_of_token("--colour-sheet-negative", ground), 4.5
                )

    def test_the_faint_grey_reads_on_a_tinted_panel_as_well_as_on_the_page(self):
        for ground in (
            "--colour-surface",
            "--colour-surface-muted",
            "--colour-surface-tint",
        ):
            with self.subTest(ground=ground):
                self.assertGreaterEqual(
                    self.ratio_of_token("--colour-ink-faint", ground), 4.5
                )


class ThePaintedComponentsClearTheirOwnFillsTest(SimpleTestCase):
    """What #732 decided, stated as ratios rather than as two hex literals.

    Three of the six pairings #732 lists are one decision each about a
    *background* or about which token a piece of live text may borrow. Each is
    measured here from the tokens in base.css rather than from a restated
    value, so moving a token is measured instead of drifting past this.
    """

    def ratio_of_token(self, token: str, ground: str) -> float:
        """Contrast between a base.css colour token and a ground it lands on.

        Args:
            token: Name of the text colour's token in base.css.
            ground: Name of the ground's token, or a literal colour.

        Returns:
            The WCAG ratio between the two, 1.0 to 21.0.
        """
        palette = custom_properties(stylesheet("base.css"))
        self.assertIn(token, palette, f"base.css no longer defines {token}")
        return contrast_ratio(palette[token].strip(), palette.get(ground, ground))

    def test_the_teal_fill_carries_white_at_full_body_contrast(self):
        """The pager's current-page chip and the open help summary.

        Neither is large text -- the chip is 15.2px regular, the summary's '?'
        is 12px bold -- so both are held to 4.5:1 rather than to the 3:1 WCAG
        allows above 18.66px bold / 24px regular.
        """
        self.assertGreaterEqual(
            self.ratio_of_token("--colour-surface", "--colour-accent-teal"), 4.5
        )

    def test_the_placeholder_grey_reads_on_the_field_it_sits_in(self):
        self.assertGreaterEqual(
            self.ratio_of_token("--colour-ink-placeholder", "--colour-surface"), 4.5
        )

    def test_the_disabled_grey_is_left_where_it_is(self):
        """Darkening it was not the decision; taking live text off it was.

        WCAG 1.4.3 exempts an inactive component, and greying one out is the
        signal that it cannot be used. --colour-ink-disabled therefore stays
        light and is spent only on inactive controls; the chat input's
        placeholder, which is live text in an enabled field, moved off it.
        """
        palette = custom_properties(stylesheet("base.css"))
        self.assertEqual(palette["--colour-ink-disabled"].strip(), "#999")

        placeholder_rules = re.findall(
            r"::placeholder[^{]*\{([^}]*)\}", stylesheet("chat.css")
        )
        self.assertEqual(len(placeholder_rules), 1)
        self.assertIn("--colour-ink-placeholder", placeholder_rules[0])

    def test_a_pool_bars_fill_is_exempt_because_it_holds_no_text(self):
        """The other three of the six: a 7px gauge, not a label (#732).

        Nothing is printed inside a .pool-bar, so measuring the page's ink
        against its fill asks a question the component never poses. The
        numbers live in the .pool-label above the bar, on the card's ground,
        where audit_text_on_ground measures them. battle.test.js holds the
        markup to that shape, so the exemption cannot quietly stop being true.
        """
        for fill in (
            ".pool-bar.body span",
            ".pool-bar.st span",
            ".pool-bar.fatigue span",
        ):
            with self.subTest(fill=fill):
                self.assertIn(fill, DECORATIVE_COMPONENTS)
                self.assertTrue(DECORATIVE_COMPONENTS[fill].strip())

    def test_the_pool_colours_are_left_where_they_are(self):
        """They say how hurt a figure is; battle.js reads them by name."""
        palette = custom_properties(stylesheet("base.css"))

        self.assertEqual(palette["--colour-pool-death"].strip(), "#b03030")
        self.assertEqual(palette["--colour-pool-fatigue"].strip(), "#2e8b57")


class AStateRuleDoesNotHideTheRestingPairingTest(SimpleTestCase):
    """A :hover ground used to file the component as painted, so rest was skipped (#736).

    :func:`_base_form` strips state pseudo-classes so a hover background is
    measured against the component's own ink rather than the page's, which is
    right and which :meth:`ColourReadingTest.
    test_a_states_background_is_measured_against_its_components_ink` states.
    But it also filed the hover rule's *background* under the plain selector,
    and :func:`audit_text_on_ground` skips any selector that paints a ground.
    A component whose only painted ground was a hover ground therefore had its
    resting pairing measured by nobody.

    ``.session-delete-btn`` is the live instance: ``background: transparent``
    at rest, so the hover rule's ``#f0f0f0`` was the only ground on file and
    its 4.77:1 the only pairing reported, while the resting ``#999`` sat on
    the chat sidebar at 2.71:1 -- on the control a reader presses to delete a
    session. Every sheet audited, no failure.

    State rules are read as a layer of their own now: a component can carry a
    resting pairing and a hover pairing and both are reported, under
    ``.session-delete-btn`` and ``.session-delete-btn:hover``.
    """

    #: The chat sidebar's own ground, which the flat audit cannot see: the
    #: button is a child of .session-item, and the background is painted by
    #: .sidebar further up. The audit measures it against the page ground
    #: instead, so the sidebar is named here the way
    #: TheDarkenedTextColoursClearTheirGroundsTest names its grounds.
    SIDEBAR_GROUND = "--colour-surface-subtle"

    def every_sheet(self) -> str:
        """base.css and every component sheet, concatenated as the page loads them."""
        theme_sheets = {
            Path(theme.stylesheet).name for theme in THEMES.values() if theme.stylesheet
        }
        return "\n".join(
            stylesheet(name)
            for name in stylesheet_names()
            if name not in theme_sheets or name == "base.css"
        )

    def test_the_session_delete_button_is_measured_at_rest(self):
        """The pairing that went unreported: the resting colour on its ground."""
        measured = {
            pairing.selector for pairing in audit_text_on_ground(self.every_sheet())
        }

        self.assertIn(".session-delete-btn", measured)

    def test_the_session_delete_buttons_hover_is_still_measured_too(self):
        """The layer does not cost us the pairing that was being reported."""
        measured = {pairing.selector for pairing in audit_theme(self.every_sheet())}

        self.assertIn(".session-delete-btn:hover", measured)

    def test_the_session_delete_button_reads_on_the_sidebar_it_sits_in(self):
        """And the colour itself: live text may not borrow the disabled grey."""
        palette = custom_properties(stylesheet("base.css"))
        resting = re.search(
            r"\.session-delete-btn\s*\{([^}]*)\}", stylesheet("chat.css")
        )
        if resting is None:
            self.fail("chat.css no longer has a resting rule for .session-delete-btn")
        token = re.search(r"color:\s*var\(\s*(--[\w-]+)", resting.group(1))
        if token is None:
            self.fail("the delete button's resting colour is not a base.css token")
        self.assertNotEqual(
            token.group(1),
            "--colour-ink-disabled",
            "the delete button is live text, not an inactive control (#736)",
        )

        self.assertGreaterEqual(
            contrast_ratio(
                palette[token.group(1)].strip(), palette[self.SIDEBAR_GROUND].strip()
            ),
            4.5,
        )

    def test_the_restore_button_carries_white_at_rest_and_not_only_on_hover(self):
        """The second live instance, found by the same change (#736).

        ``.restore-btn`` fills with --colour-restore and prints
        --colour-surface on it. That was #28a745 under white, 3.13:1 -- and
        the hover rule's darker fill was the only ground on file, so 4.52:1
        was the only pairing reported. Both fills are measured now, and the
        hover stays a step darker than the resting one so pressing the button
        still reads as a change.
        """
        palette = custom_properties(stylesheet("base.css"))
        white = palette["--colour-surface"].strip()
        resting = palette["--colour-restore"].strip()
        hover = palette["--colour-restore-strong"].strip()

        self.assertGreaterEqual(contrast_ratio(white, resting), 4.5)
        self.assertGreaterEqual(contrast_ratio(white, hover), 4.5)
        self.assertGreater(
            contrast_ratio(white, hover),
            contrast_ratio(white, resting),
            "the hover fill is meant to be the darker of the two",
        )

    def test_the_battle_tabs_live_dot_is_exempt_because_it_holds_no_text(self):
        """A pseudo-element is a box of its own, and this one is empty.

        Its fill used to be filed under ``.gm-panel-tab-button.is-live``, so
        exempting the dot would have exempted the tab's own label as well.
        Keeping ``::after`` in the key is what lets the entry name the dot.
        """
        dot = ".gm-panel-tab-button.is-live::after"

        self.assertIn(dot, DECORATIVE_COMPONENTS)
        self.assertTrue(DECORATIVE_COMPONENTS[dot].strip())
        self.assertNotIn(".gm-panel-tab-button.is-live", DECORATIVE_COMPONENTS)

    def test_a_component_whose_only_ground_is_a_hover_ground_is_caught(self):
        """The shape, stated as a fixture so the guard can be watched failing."""
        base = (
            "body { background: #ffffff; color: #333333; }"
            ".quiet-btn { background: transparent; color: #999999; }"
            ".quiet-btn:hover { background: #f0f0f0; color: #c0392b; }"
        )

        unreadable = failures(base, "", "default")

        # #999999 on the page's white is 2.85:1, and it renders at rest.
        self.assertEqual([pairing.selector for pairing in unreadable], [".quiet-btn"])

    def test_the_same_component_passes_once_its_resting_colour_does(self):
        """So the failure above is the colour's doing, not the fixture's."""
        base = (
            "body { background: #ffffff; color: #333333; }"
            ".quiet-btn { background: transparent; color: #666666; }"
            ".quiet-btn:hover { background: #f0f0f0; color: #c0392b; }"
        )

        self.assertEqual(failures(base, "", "default"), [])

    def test_a_state_that_paints_a_ground_is_reported_under_its_own_name(self):
        """So a failure says which state to look at rather than only which box."""
        base = (
            "body { background: #ffffff; color: #333333; }"
            ".tab { background: #ffffff; color: #333333; }"
            ".tab:hover { background: #eeeeee; color: #dddddd; }"
        )

        unreadable = failures(base, "", "default")

        self.assertEqual([pairing.selector for pairing in unreadable], [".tab:hover"])

    def test_a_focus_visible_rule_is_not_read_as_a_selector_called_visible(self):
        """The pseudo list matched `focus` first and left `-visible` behind.

        `.choice-btn:focus-visible` reduced to `.choice-btn-visible`, a
        component no sheet names, so its ground found no ink but the page's
        and the real pairing -- the button's own ink on its hover fill -- was
        never measured. Nothing failed, because the page's ink happened to
        clear the fill.
        """
        base = (
            "body { background: #ffffff; color: #333333; }"
            ".tab { background: #ffffff; color: #ffffff; }"
            ".tab:focus-visible { background: #fafafa; }"
        )

        unreadable = failures(base, "", "default")

        self.assertEqual(
            [pairing.selector for pairing in unreadable], [".tab", ".tab:focus-visible"]
        )


class TheGuardCatchesTheBugItWasWrittenForTest(SimpleTestCase):
    """A guard nobody has watched fail is not yet known to work."""

    BASE = """
        body { background: #ffffff; color: #333333; }
        .card { background: #f5f5f5; }
        .plain { color: #555555; }
    """

    def test_a_theme_that_moves_the_ground_and_leaves_a_panel_behind_is_caught(self):
        # Exactly #306's shape: the ink goes light, .card keeps its light
        # background, and the two meet on the page.
        theme = '[data-theme="x"] body { background: #222222; color: #eeeeee; }'

        unreadable = failures(self.BASE, theme, "x")

        self.assertIn(".card", [pairing.selector for pairing in unreadable])

    def test_the_same_theme_passes_once_the_panel_comes_with_it(self):
        # .plain is repainted too. It paints no ground of its own, so before
        # audit_text_on_ground (issue #709) nothing measured it and this theme
        # passed while leaving #555555 text on a #222222 page -- 1.50:1. The
        # fixture now states the whole job a theme has to do.
        theme = (
            '[data-theme="x"] body { background: #222222; color: #eeeeee; }'
            '[data-theme="x"] .card { background: #333333; }'
            '[data-theme="x"] .plain { color: #cccccc; }'
        )

        self.assertEqual(failures(self.BASE, theme, "x"), [])

    def test_a_theme_that_moves_the_ground_and_leaves_ground_text_behind_is_caught(
        self,
    ):
        """#709: the chrome sets a colour and no background, so nothing saw it."""
        theme = (
            '[data-theme="x"] body { background: #222222; color: #eeeeee; }'
            '[data-theme="x"] .card { background: #333333; }'
        )

        unreadable = failures(self.BASE, theme, "x")

        self.assertIn(".plain", [pairing.selector for pairing in unreadable])

    def test_the_default_theme_of_that_fixture_is_clean(self):
        """So the failure above is the theme's doing, not the fixture's."""
        self.assertEqual(failures(self.BASE, "", "default"), [])


class ColourReadingTest(SimpleTestCase):
    """What the audit will and will not draw a conclusion from."""

    def test_a_colour_keyword_is_understood(self):
        base = "body { background: #ffffff; color: #333333; }"
        theme = '[data-theme="x"] .chip { background: #c82333; color: white; }'

        # white on #c82333 is 5.9:1 -- readable, so no failure is reported.
        self.assertEqual(failures(base, theme, "x"), [])

    def test_a_custom_property_is_resolved_before_measuring(self):
        base = "body { background: #ffffff; color: #333333; }"
        theme = (
            '[data-theme="x"] { --ink: #f0f0f0; }'
            '[data-theme="x"] .chip { background: #fafafa; color: var(--ink); }'
        )

        self.assertIn(
            ".chip", [pairing.selector for pairing in failures(base, theme, "x")]
        )

    def test_a_translucent_colour_is_skipped_rather_than_guessed_at(self):
        """An alpha channel makes the rendered colour depend on what is behind."""
        base = "body { background: #ffffff; color: #333333; }"
        theme = '[data-theme="x"] .chip { background: #ffffff11; }'

        self.assertEqual(failures(base, theme, "x"), [])

    def test_a_states_background_is_measured_against_its_components_ink(self):
        """A :hover background carries the component's text colour, not the page's."""
        base = (
            "body { background: #ffffff; color: #333333; }"
            ".button { background: #333333; color: white; }"
            ".button.danger:hover { background: #a00000; }"
        )

        # white on #a00000 is 7.0:1. Pairing it with the page's #333 ink
        # instead would invent a failure at 1.6:1.
        self.assertEqual(failures(base, "", "default"), [])

    def test_an_inactive_control_is_exempt(self):
        """WCAG 1.4.3 exempts inactive components; greying out is the signal."""
        base = (
            "body { background: #ffffff; color: #333333; }"
            "input:disabled { background: #f5f5f5; color: #999999; }"
        )

        self.assertEqual(failures(base, "", "default"), [])

    def test_a_decorative_box_is_exempt(self):
        """The hamburger bars hold no text, so a ratio for them means nothing."""
        base = (
            "body { background: #ffffff; color: #333333; }"
            ".menu-toggle span { width: 24px; height: 2px; background: #333333; }"
        )

        self.assertEqual(failures(base, "", "default"), [])


class ColoursComeFromBaseTokensTest(SimpleTestCase):
    """Only base.css may write a hex colour; every other sheet names a token (#338)."""

    def test_no_sheet_but_base_paints_with_a_hex_literal(self):
        theme_sheets = {
            Path(theme.stylesheet).name for theme in THEMES.values() if theme.stylesheet
        }
        names = stylesheet_names()
        self.assertGreater(len(names), 10)
        for name in names:
            if name == "base.css":
                continue
            with self.subTest(sheet=name):
                literals = colour_literals(
                    stylesheet(name),
                    allow_custom_properties=name in theme_sheets,
                )
                self.assertEqual(
                    literals,
                    [],
                    f"{name} paints with literals; add a token to base.css",
                )

    def test_every_sheets_comments_close_where_they_mean_to(self):
        """A '*/' inside a comment ends it early and eats the next declaration.

        Both this guard and the browser strip comments the same shortest-match
        way, so an unbalanced comment would hide a token from the browser while
        the audit still saw it.
        """
        for name in stylesheet_names():
            with self.subTest(sheet=name):
                outside = re.sub(r"/\*.*?\*/", "", stylesheet(name), flags=re.DOTALL)
                self.assertNotIn("*/", outside)
                self.assertNotIn("/*", outside)

    def test_a_literal_in_a_declaration_is_caught(self):
        self.assertEqual(
            colour_literals(".x { color: #666; }", allow_custom_properties=False),
            ["#666"],
        )

    def test_a_token_reference_is_not_a_literal(self):
        css = ".x { color: var(--colour-ink-muted); }"
        self.assertEqual(colour_literals(css, allow_custom_properties=False), [])

    def test_an_issue_number_in_a_comment_is_not_a_colour(self):
        css = "/* issue #312 */ .x { color: var(--colour-ink); } /* #abc */"
        self.assertEqual(colour_literals(css, allow_custom_properties=False), [])

    def test_a_theme_may_define_its_own_token_but_not_paint_directly(self):
        css = '[data-theme="x"] { --accent: #443a86; } .y { color: #666; }'
        self.assertEqual(colour_literals(css, allow_custom_properties=True), ["#666"])
        self.assertEqual(
            colour_literals(css, allow_custom_properties=False), ["#443a86", "#666"]
        )

    def test_a_private_token_set_in_a_component_sheet_is_caught(self):
        css = ".page { --ink: #2b2b2b; }"
        self.assertEqual(
            colour_literals(css, allow_custom_properties=False), ["#2b2b2b"]
        )


class EveryTokenASheetUsesIsDefinedTest(SimpleTestCase):
    """A var() naming a token nobody defined renders as nothing (#461)."""

    def test_no_sheet_uses_an_undefined_token_without_a_fallback(self):
        sheets = {name: stylesheet(name) for name in stylesheet_names()}
        self.assertGreater(len(sheets), 10)

        self.assertEqual(undefined_custom_properties(sheets), [])

    def test_an_undefined_token_is_caught(self):
        sheets = {
            "base.css": ":root { --colour-ink: #333; }",
            "battle.css": ".note { background: var(--battle-panel); }",
        }

        self.assertEqual(
            undefined_custom_properties(sheets), ["battle.css: --battle-panel"]
        )

    def test_a_token_defined_in_another_sheet_or_given_a_fallback_is_allowed(self):
        sheets = {
            "base.css": ":root { --colour-ink: #333; }",
            "chat.css": (
                ".a { color: var(--colour-ink); }"
                ".b { color: var(--colour-accent, var(--colour-ink)); }"
                "/* var(--only-in-a-comment) */"
            ),
        }

        self.assertEqual(undefined_custom_properties(sheets), [])


class DeletedBattleNoteIsReadableTest(SimpleTestCase):
    """The soft-deleted battle note has a ground, and its text reads on it (#461)."""

    def test_the_note_has_the_warm_ground_under_every_theme(self):
        base_and_battle = stylesheet("base.css") + "\n" + stylesheet("battle.css")
        for theme in THEMES.values():
            with self.subTest(theme=theme.key):
                theme_sheet = (
                    stylesheet(Path(theme.stylesheet).name) if theme.stylesheet else ""
                )
                pairings = {
                    pairing.selector: pairing
                    for pairing in audit_theme(base_and_battle, theme_sheet, theme.key)
                }

                self.assertIn(".battle-deleted-note", pairings)
                note = pairings[".battle-deleted-note"]
                self.assertEqual(note.background.lower(), "#faf9f6")
                self.assertTrue(note.passes, str(note))


class ThemePrefixTest(SimpleTestCase):
    """A theme's rules must key to the components base.css paints."""

    def test_a_themes_override_replaces_the_base_rule_it_targets(self):
        base = "body { background: #ffffff; color: #333333; } .card { background: #f5f5f5; }"
        theme = (
            '[data-theme="x"] body { background: #222222; color: #eeeeee; }'
            '[data-theme="x"] .card { background: #f5f5f5; }'
        )

        # The theme restates the light panel under its dark ink: still broken,
        # and reported once rather than twice.
        unreadable = failures(base, theme, "x")

        self.assertEqual([pairing.selector for pairing in unreadable], [".card"])


class ConditionalBlocksAreAuditedTest(SimpleTestCase):
    """@media and @supports blocks, which used to be half-read (issue #681).

    The flat rule regex took ``@media (...)`` as a selector and swallowed the
    block's first rule as its declarations -- dropping that rule -- while
    every later rule in the block was audited as if it were unconditional. A
    dark-mode block that moves ``body``'s ink is the #306 shape this module
    exists to catch, so it was a hole in the guard rather than a detail.
    """

    BASE = """
        body { background: #ffffff; color: #111111; }
        .card { background: #ffffff; }
        @media (prefers-color-scheme: dark) {
            body { background: #101010; color: #f5f5f5; }
            .panel { background: #151515; }
        }
        .after-the-block { background: #ffffff; }
    """

    def test_a_dark_mode_block_that_leaves_a_panel_light_is_caught(self):
        """The failure the module exists for, inside a conditional block."""
        sheet = """
            body { background: #ffffff; color: #111111; }
            .card { background: #ffffff; }
            @media (prefers-color-scheme: dark) {
                body { background: #101010; color: #f5f5f5; }
            }
        """

        unreadable = failures(sheet, "", "tarmar")

        self.assertEqual([pairing.selector for pairing in unreadable], [".card"])
        self.assertIn("prefers-color-scheme: dark", unreadable[0].theme)

    def test_the_first_rule_in_a_block_is_not_swallowed(self):
        """It was taken as the @media selector's declarations and dropped."""
        layers = audit_conditionals(self.BASE, "", "tarmar")

        painted = {pairing.selector for pairing in layers}
        self.assertIn("body", painted)
        self.assertIn(".panel", painted)

    def test_a_blocks_rules_are_not_audited_as_unconditional(self):
        """`.panel` exists only in the dark block; the light sheet has none."""
        unconditional = {
            pairing.selector for pairing in audit_theme(self.BASE, "", "tarmar")
        }

        self.assertNotIn(".panel", unconditional)
        self.assertIn(".after-the-block", unconditional)

    def test_the_layer_is_named_after_the_condition_it_holds_under(self):
        """A failure has to say which block to open."""
        names = {pairing.theme for pairing in audit_conditionals(self.BASE, "", "x")}

        self.assertEqual(names, {"x @media (prefers-color-scheme: dark)"})

    def test_a_token_redefined_in_a_block_does_not_leak_out_of_it(self):
        """_variables scanned the whole sheet flat, later winning."""
        sheet = """
            :root { --ink: #111111; }
            body { background: #ffffff; color: var(--ink); }
            @media (prefers-color-scheme: dark) {
                :root { --ink: #f5f5f5; }
                body { background: #101010; }
            }
        """

        unconditional = {
            pairing.selector: pairing for pairing in audit_theme(sheet, "", "tarmar")
        }

        # Light mode still reads its own ink, not the dark block's.
        self.assertEqual(unconditional["body"].foreground.lower(), "#111111")

    def test_the_live_sheets_really_do_have_layers_to_audit(self):
        """Guards the tests above: no blocks would make them vacuous.

        base.css's own three blocks move layout and nothing else, so what they
        change is legitimately empty and asserting otherwise would only pin a
        fact about base.css that is not the audit's doing. Both facts are
        stated: the blocks exist, and across every sheet the site loads two
        components are painted inside a block and reported from it.
        """
        base = stylesheet("base.css")
        self.assertGreater(len(conditional_layers(base)), 0)
        self.assertEqual(audit_conditionals(base, "", "tarmar"), [])

        theme_sheets = {
            Path(theme.stylesheet).name for theme in THEMES.values() if theme.stylesheet
        }
        every_sheet = "\n".join(
            stylesheet(name) for name in stylesheet_names() if name not in theme_sheets
        )
        self.assertGreater(len(audit_conditionals(every_sheet, "", "tarmar")), 0)

    def test_a_block_that_moves_only_layout_reports_nothing(self):
        """#732: an unconditional pairing used to come back from every block.

        Six failing pairings read as 138 across the component sheets' 23
        blocks, which is what made running the whole audit look unapproachable.
        A block that repaints nothing has changed nothing to report.
        """
        sheet = """
            body { background: #ffffff; color: #111111; }
            .card { background: #f5f5f5; }
            @media (max-width: 600px) {
                .card { padding: 0.5rem; display: block; }
            }
        """

        self.assertEqual(audit_conditionals(sheet, "", "tarmar"), [])

    def test_a_failure_outside_every_block_is_reported_once(self):
        """Once by audit_theme, and not again by each block it is visible in."""
        sheet = """
            body { background: #ffffff; color: #111111; }
            .card { background: #f5f5f5; color: #eeeeee; }
            @media (max-width: 600px) { .card { padding: 0.5rem; } }
            @media (max-width: 900px) { .card { margin: 0; } }
            @media print { .card { border: 0; } }
        """

        unreadable = failures(sheet, "", "tarmar")

        self.assertEqual([pairing.selector for pairing in unreadable], [".card"])
        self.assertEqual(unreadable[0].theme, "tarmar")

    def test_a_block_that_repaints_a_component_is_still_reported(self):
        """Filtering out what a block leaves alone must not filter out its own work."""
        sheet = """
            body { background: #ffffff; color: #111111; }
            .card { background: #f5f5f5; }
            @media (max-width: 600px) {
                .card { background: #2a2a2a; }
            }
        """

        unreadable = failures(sheet, "", "tarmar")

        self.assertEqual([pairing.selector for pairing in unreadable], [".card"])
        self.assertIn("max-width: 600px", unreadable[0].theme)
