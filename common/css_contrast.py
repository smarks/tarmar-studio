"""Static contrast audit of the stylesheets, so a theme cannot go unreadable.

Issue #306: ``theme-classic-arena.css`` was an override layer over a *light*
``base.css``. It repainted the page's ground and ink but only 21 selectors,
so every component base paints light kept its light background and inherited
the theme's near-white ink -- 32 pairings below WCAG AA, several at 1.0:1,
which is white on white. The battle log and every error notice were among
them, and nothing failed: the sheets were valid CSS and the app rendered.

This module is what makes that class of defect fail a build instead. It reads
the sheets, works out what colour text lands on what colour ground under each
theme, and reports every pairing beneath the threshold.

**What it models, and what it does not.** A browser resolves colour through
the full cascade -- specificity, source order, inheritance through the real
DOM. Reimplementing that would be a project. This models the one shape the
sheets are actually written in, which is also the shape the bug took:

- a rule paints a component's background;
- the text on it is that component's own ``color`` if any rule mentioning the
  component sets one, and otherwise the ink inherited from ``body``;
- a theme changes ``body``'s ink and ground, and may repaint components.

So it catches "the ground moved and this component did not come with it",
which is the failure that shipped. It does not catch a clash produced only by
a selector's position in the cascade, and it does not see colour set from
JavaScript or from a template's inline style.

A rule for a *state* -- ``:hover``, ``:focus``, ``:active`` and their kin --
is read as a layer over the resting component rather than merged into it, and
so is a rule for a pseudo-element. Merging them is how a component could hide
its resting pairing behind a hover one: ``.session-delete-btn`` painted
nothing at rest, its ``:hover`` ground was filed under the plain selector, and
:func:`audit_text_on_ground` skips a selector that paints its own ground --
so the 4.77:1 of the hover was reported and the 2.71:1 a reader actually looks
at was reported by nobody (issue #736). Both layers are measured now and named
apart, ``.x`` and ``.x:hover``, so a failure says which state to look at.

``@media`` and ``@supports`` blocks are audited as layers of their own: each
one is read as an override on top of the unconditional sheet and reported
under its own name, e.g. ``tarmar @media (prefers-color-scheme: dark)``. They
used to be neither -- the flat rule regex took ``@media (...)`` as a selector
and swallowed the block's first rule as its declarations, dropping it, while
every later rule in the block was audited as though it were unconditional
(issue #681). A dark-mode block moving ``body``'s ink is exactly the #306
shape this module exists to catch, so the blocks are now read rather than
misread. Those limits are the price of
a check that needs no browser and runs in the unit suite; the Playwright
theme spec covers what a real render proves.

Only opaque ``#rgb``/``#rrggbb`` colours participate. ``transparent``,
``currentColor``, gradients and ``rgba()`` are skipped rather than guessed at:
a wrong guess here would either mask a real failure or invent a false one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# WCAG 2.x AA. Body text needs 4.5:1; text at or above 18.66px bold / 24px
# regular needs 3:1. The sheets do not record which is which, so everything is
# held to the stricter number -- a component that genuinely renders large can
# be granted an exception in LARGE_TEXT_COMPONENTS with a reason.
AA_NORMAL_TEXT = 4.5
AA_LARGE_TEXT = 3.0

# Components whose text is large enough for the 3:1 threshold. Each entry needs
# a reason, so the list stays a record of decisions rather than a place to put
# whatever is currently failing.
LARGE_TEXT_COMPONENTS: dict[str, str] = {}

_HEX = r"#[0-9a-fA-F]{3,8}\b"
_RULE = re.compile(r"([^{}]+)\{([^}]*)\}", re.MULTILINE)
_VAR_DEF = re.compile(r"(--[\w-]+)\s*:\s*([^;]+)")
_VAR_USE = re.compile(r"var\(\s*(--[\w-]+)\s*(?:,\s*([^)]+))?\)")
# An at-rule whose body holds rules, up to and including its opening brace.
_CONDITIONAL_AT_RULE = re.compile(r"@(?:media|supports)[^{}]*\{")

# The colour keywords these sheets actually use. Deliberately not the full CSS
# list: a name that appears here is one somebody wrote, and an unknown name is
# skipped rather than guessed at.
COLOUR_KEYWORDS = {"white": "#ffffff", "black": "#000000"}

# State and structural pseudo-classes. A rule for a state paints the same
# component as the rule without it, so `.button.danger:hover`'s text colour is
# whatever `.button` sets -- pairing a hover background with the page's
# inherited ink instead is how this check invents failures that do not exist.
#
# The longer spellings come first: alternation is ordered, so `focus` used to
# match inside `:focus-visible` and leave `-visible` behind, which turned
# `.choice-btn:focus-visible` into `.choice-btn-visible` -- a component no
# sheet names, whose ink therefore came from the page rather than from the
# button (issue #736).
_PSEUDO = re.compile(
    r"::?(?:hover|focus-visible|focus-within|focus|active|visited|checked|"
    r"first-child|last-child|only-child|first-of-type|last-of-type|"
    r"nth-child\([^)]*\)|nth-of-type\([^)]*\)|not\([^)]*\)|empty|"
    r"before|after|placeholder|marker|selection|read-only)"
)

# The pseudo-classes above that are *states*: something the reader is doing to
# the component, not a structural fact about it. A state rule is read as a
# layer over the resting component rather than merged into it, so a component
# can carry a resting pairing and a hover pairing and both get measured
# (issue #736).
_STATE_PSEUDO = re.compile(
    r":(?:hover|focus-visible|focus-within|focus|active|visited|checked)\b"
)

# Pseudo-elements. One of these is a box of its own rather than the component,
# so a ground it paints belongs to it: filing `.tab.is-live::after`'s fill
# under `.tab.is-live` says the tab's label stands on the little status dot,
# which it does not. They key apart for the same reason states do, which is
# also what lets DECORATIVE_COMPONENTS name a dot without exempting the
# control it hangs off (issue #736).
_PSEUDO_ELEMENT = re.compile(r"::(?:before|after|placeholder|marker|selection)\b")

# One reason shared by the three pool-bar fills below, which differ only in
# which pool they colour.
_POOL_BAR_FILL = (
    "the coloured fill of a combatant's 7px condition gauge: an empty <span> "
    "that battle.js sizes by setting its width, inside a .pool-bar that "
    "renders no text of its own. The numbers are the .pool-label above the "
    "bar, on the card's own ground (issue #732)"
)

# Boxes that never hold text, so a contrast ratio for them is meaningless.
# Each needs a reason: this is a record of judgements, not a drawer for
# whatever happens to be failing.
DECORATIVE_COMPONENTS: dict[str, str] = {
    ".menu-toggle span": "the hamburger icon's three bars -- shape, no text",
    ".breadcrumb li + li": (
        "a '\u203a' separator glyph, deliberately de-emphasised; WCAG 1.4.3 "
        "exempts incidental decoration"
    ),
    ".menu-divider": "a horizontal rule between menu groups",
    ".pool-bar.body span": _POOL_BAR_FILL,
    ".pool-bar.st span": _POOL_BAR_FILL,
    ".pool-bar.fatigue span": _POOL_BAR_FILL,
    ".gm-panel-tab-button.is-live::after": (
        "the 8px gold dot that marks the Battle tab while a fight is under "
        'way (issue #345): an empty ::after with content: "", hanging off '
        "the label rather than holding it. The label is the button's own "
        "text, on the tab strip's ground, and is measured there (issue #736)"
    ),
}


def relative_luminance(colour: str) -> float:
    """WCAG relative luminance of an opaque ``#rgb`` or ``#rrggbb`` colour."""
    digits = colour.lstrip("#")
    if len(digits) == 3:
        digits = "".join(character * 2 for character in digits)
    channels = [int(digits[index : index + 2], 16) / 255 for index in (0, 2, 4)]
    linear = [
        channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG contrast ratio between two opaque colours, from 1.0 to 21.0."""
    first, second = relative_luminance(foreground), relative_luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def _colour(value: str) -> str | None:
    """The one opaque colour in a declaration, or None if there is no usable one.

    A value carrying several colours (a gradient, a multi-part border shorthand)
    is skipped: which one the text lands on is a rendering question this cannot
    answer, and guessing would be worse than staying quiet.
    """
    cleaned = value.strip().rstrip(";").strip()
    keyword = COLOUR_KEYWORDS.get(cleaned.lower())
    if keyword:
        return keyword
    found = re.findall(_HEX, cleaned)
    if len(found) != 1:
        return None
    digits = found[0].lstrip("#")
    if len(digits) not in (3, 6):  # #rgba / #rrggbbaa are not opaque
        return None
    return found[0]


@dataclass(frozen=True)
class Pairing:
    """One text-on-background pairing the sheets produce, with its ratio."""

    theme: str
    selector: str
    foreground: str
    background: str
    ratio: float
    threshold: float

    @property
    def passes(self) -> bool:
        return self.ratio >= self.threshold

    def __str__(self) -> str:
        return (
            f"{self.theme}: {self.selector} -- {self.foreground} on "
            f"{self.background} is {self.ratio:.2f}:1, needs {self.threshold}:1"
        )


_THEME_PREFIX = re.compile(r"^(?::root|\[data-theme=[^\]]*\])\s*")

# WCAG 1.4.3 exempts "inactive user interface components" from the contrast
# minimum: a greyed-out control is signalling that it cannot be used, and
# forcing it to 4.5:1 would defeat the signal. Both spellings count: the
# `:disabled` pseudo-class, and the `.disabled` class base.css puts on a menu
# entry it has also given `pointer-events: none` -- that entry is as inactive
# as a disabled control, and only the pseudo-class was recognised before
# (issue #709).
_INACTIVE = re.compile(r"(?::disabled|\.disabled)\b")

# The scrollbar's parts (`::-webkit-scrollbar`, `-track`, `-thumb` and the
# rest) are boxes a browser draws around scrolled content. No text is ever set
# on one, so the pairing this module would invent for it, the page's ink on
# the part's ground, is not one a reader meets. The ratio that applies to a
# thumb is its contrast with its track, a graphic under WCAG 1.4.11 (3:1),
# which the stylesheet's own tests state (issue #63).
_SCROLLBAR_PART = re.compile(r"::-webkit-scrollbar\b")


def _base_form(selector: str) -> str:
    """The component a selector paints, comparable across sheets.

    Strips the theme's ``[data-theme="..."]`` prefix so a theme's override of
    ``header`` keys to the same component as base.css's ``header`` -- without
    that they never meet, and the check cannot see a theme repainting the
    page's ink out from under a component it left alone, which is the whole
    failure #306 was about.
    """
    without_theme = _THEME_PREFIX.sub("", selector)
    return " ".join(_PSEUDO.sub("", without_theme).split()).strip()


def _is_layer(found: re.Match[str]) -> bool:
    """Whether one matched pseudo is a layer of its own rather than the component."""
    return bool(
        _STATE_PSEUDO.fullmatch(found.group())
        or _PSEUDO_ELEMENT.fullmatch(found.group())
    )


def _stated_form(selector: str) -> str:
    """The component a selector paints, with the state or pseudo-element kept.

    :func:`_base_form` with the state pseudo-classes and pseudo-elements left
    in place, so ``[data-theme="x"] .button.danger:hover`` keys to
    ``.button.danger:hover`` and ``.tab.is-live::after`` to itself. Two rules
    for the same component in two different states key apart here and are
    measured as two pairings (issue #736).
    """
    without_theme = _THEME_PREFIX.sub("", selector)
    kept = _PSEUDO.sub(
        lambda found: found.group() if _is_layer(found) else "", without_theme
    )
    return " ".join(kept.split()).strip()


def _is_layer_rule(selector: str) -> bool:
    """Whether a selector paints a state or a pseudo-element rather than the box."""
    without_theme = _THEME_PREFIX.sub("", selector)
    return bool(
        _STATE_PSEUDO.search(without_theme) or _PSEUDO_ELEMENT.search(without_theme)
    )


@dataclass(frozen=True)
class PaintedColours:
    """What a set of sheets paints, with the state layer kept off the resting one.

    Merging the two is the defect #736 was filed for: a ``:hover`` background
    filed under the plain selector made the component look as though it
    painted its own ground, and :func:`audit_text_on_ground` skips a selector
    that does -- so a component whose only painted ground was a hover ground
    had its resting pairing measured by nobody.

    Attributes:
        resting_backgrounds: Component to the ground it paints at rest.
        resting_foregrounds: Component to the text colour it sets at rest.
        state_backgrounds: ``".x:hover"`` to the ground that state paints.
        state_foregrounds: ``".x:hover"`` to the text colour that state sets.
    """

    resting_backgrounds: dict[str, str]
    resting_foregrounds: dict[str, str]
    state_backgrounds: dict[str, str]
    state_foregrounds: dict[str, str]

    @property
    def page_ground(self) -> str:
        """The ground ``body`` paints, or white when no sheet says."""
        return self.resting_backgrounds.get("body", "#ffffff")

    @property
    def inherited_ink(self) -> str:
        """The ink ``body`` sets, which every component inherits by default."""
        return self.resting_foregrounds.get("body", "#000000")

    def ink_for(self, component: str) -> str:
        """The text colour a component at rest renders, following its base rules.

        Args:
            component: A resting selector, as :func:`_base_form` spells it.

        Returns:
            The nearest colour the sheets set for it, else the inherited ink.
        """
        for candidate in _generalisations(component):
            if candidate in self.resting_foregrounds:
                return self.resting_foregrounds[candidate]
        return self.inherited_ink

    def ink_in_state(self, stated: str) -> str:
        """The text colour a component renders in one state.

        A state rule that sets no colour of its own shows the component's
        resting ink over its state ground, which is what ``.button.danger:hover``
        does and what :func:`_base_form` was written to model.

        Args:
            stated: A state selector, as :func:`_stated_form` spells it.

        Returns:
            The state's own colour if it sets one, else the resting ink.
        """
        if stated in self.state_foregrounds:
            return self.state_foregrounds[stated]
        return self.ink_for(_base_form(stated))

    def ground_under(self, selector: str) -> str:
        """The ground a text colour lands on when it paints none of its own.

        The nearest ancestor named in the selector that paints a resting
        ground, falling back to the page's. A state's ground is its own if it
        paints one, else the component's resting ground, else this.

        Args:
            selector: The text's selector, resting or stated.

        Returns:
            An opaque ``#rrggbb`` colour.
        """
        parts = _base_form(selector).split()
        for depth in range(len(parts) - 1, 0, -1):
            ancestor = " ".join(parts[:depth])
            if ancestor in self.resting_backgrounds:
                return self.resting_backgrounds[ancestor]
        return self.page_ground


def _painted(base_css: str, theme_css: str) -> PaintedColours:
    """Read every colour the sheets paint, resting rules apart from state rules.

    Args:
        base_css: The site-wide sheet.
        theme_css: The theme's override sheet, or "" for the default theme.

    Returns:
        The four maps, later rules winning over earlier ones.
    """
    variables = {**_variables(base_css), **_variables(theme_css)}
    painted = PaintedColours({}, {}, {}, {})
    for selector, declarations in list(_rules(base_css)) + list(_rules(theme_css)):
        stated = _is_layer_rule(selector)
        key = _stated_form(selector) if stated else _base_form(selector)
        backgrounds = (
            painted.state_backgrounds if stated else painted.resting_backgrounds
        )
        foregrounds = (
            painted.state_foregrounds if stated else painted.resting_foregrounds
        )
        for property_name in ("background", "background-color"):
            if property_name in declarations:
                colour = _colour(_resolve(declarations[property_name], variables))
                if colour:
                    backgrounds[key] = colour
        if "color" in declarations:
            colour = _colour(_resolve(declarations["color"], variables))
            if colour:
                foregrounds[key] = colour
    return painted


def _exempt(selector: str) -> bool:
    """Whether a selector is one WCAG or this module deliberately does not measure.

    Both spellings are looked up, so an entry may name a whole component
    (``.menu-toggle span``) or one pseudo-element of it (``.tab.is-live::after``,
    the status dot) without exempting the control that carries it.
    """
    return (
        selector in DECORATIVE_COMPONENTS
        or _base_form(selector) in DECORATIVE_COMPONENTS
        or bool(_INACTIVE.search(selector))
        or bool(_SCROLLBAR_PART.search(selector))
    )


def _threshold(selector: str) -> float:
    """The ratio a selector's text is held to: 3:1 only where it renders large."""
    return (
        AA_LARGE_TEXT
        if _base_form(selector) in LARGE_TEXT_COMPONENTS
        else AA_NORMAL_TEXT
    )


def _generalisations(selector: str):
    """The selector, then it with trailing classes dropped from its last compound.

    ``.button.danger`` yields ``.button.danger`` then ``.button``, so a
    background painted on the modifier finds the text colour set on the base
    component -- which is where these sheets put it.
    """
    yield selector
    parts = selector.split()
    if not parts:
        return
    last = parts[-1]
    pieces = re.findall(r"[.#]?[\w-]+|\[[^\]]*\]", last)
    while len(pieces) > 1:
        pieces = pieces[:-1]
        yield " ".join(parts[:-1] + ["".join(pieces)]).strip()


def _declarations(block: str) -> dict[str, str]:
    """Property to value for one rule body, last declaration winning."""
    result: dict[str, str] = {}
    for piece in block.split(";"):
        if ":" not in piece:
            continue
        name, _, value = piece.partition(":")
        name = name.strip().lower()
        if name.startswith("--") or not name:
            continue
        result[name] = value.strip()
    return result


def _resolve(value: str, variables: dict[str, str]) -> str:
    """Expand ``var(--x)`` (and its fallback) against a variable table."""
    for _ in range(8):  # variables chain; a cycle stops here rather than hangs
        match = _VAR_USE.search(value)
        if match is None:
            return value
        replacement = variables.get(match.group(1), (match.group(2) or "").strip())
        value = value[: match.start()] + replacement + value[match.end() :]
    return value


def _without_comments(css: str) -> str:
    """The sheet with its comments removed.

    They cite issue numbers like ``#312``, which read as colours, and an
    ``@media`` inside one would otherwise open a block that never closes.
    """
    return re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)


@dataclass(frozen=True)
class ConditionalBlock:
    """One ``@media`` or ``@supports`` block: its condition and its CSS."""

    condition: str
    css: str


def _split_conditionals(css: str) -> tuple[str, list[ConditionalBlock]]:
    """Separate a sheet's unconditional CSS from its conditional blocks.

    The rule regex is flat and has no notion of nesting, so everything that
    reads rules reads this function's first return value, and each block is
    audited on its own (issue #681).

    Args:
        css: A stylesheet's text, comments already removed.

    Returns:
        The CSS outside any ``@media``/``@supports`` block, and one
        :class:`ConditionalBlock` per block, in source order.
    """
    unconditional: list[str] = []
    blocks: list[ConditionalBlock] = []
    position = 0
    while True:
        opening = _CONDITIONAL_AT_RULE.search(css, position)
        if opening is None:
            unconditional.append(css[position:])
            return "".join(unconditional), blocks
        unconditional.append(css[position : opening.start()])
        depth = 1
        index = opening.end()
        while index < len(css) and depth:
            if css[index] == "{":
                depth += 1
            elif css[index] == "}":
                depth -= 1
            index += 1
        body_ends = index - 1 if depth == 0 else index
        blocks.append(
            ConditionalBlock(
                condition=" ".join(opening.group()[:-1].split()),
                css=css[opening.end() : body_ends],
            )
        )
        position = index


def _rules(css: str):
    """Selector and declarations for each rule, one entry per comma-separated part.

    Conditional blocks are left out; :func:`audit_conditionals` reads those.
    """
    stripped, _ = _split_conditionals(_without_comments(css))
    for match in _RULE.finditer(stripped):
        selector_list = match.group(1).strip()
        if not selector_list or selector_list.startswith("@"):
            continue
        declarations = _declarations(match.group(2))
        for selector in selector_list.split(","):
            selector = " ".join(selector.split()).strip()
            if selector:
                yield selector, declarations


def _variables(css: str) -> dict[str, str]:
    """Every unconditional custom property in a sheet, later winning.

    A property redefined inside a conditional block used to override the
    unconditional one for the whole audit, because this scanned the sheet
    flat (issue #681). Those definitions belong to their own layer now.
    """
    stripped, _ = _split_conditionals(_without_comments(css))
    return {
        match.group(1): match.group(2).strip() for match in _VAR_DEF.finditer(stripped)
    }


def conditional_layers(*sheets: str) -> list[ConditionalBlock]:
    """Every ``@media``/``@supports`` block across ``sheets``, in order."""
    blocks: list[ConditionalBlock] = []
    for sheet in sheets:
        blocks.extend(_split_conditionals(_without_comments(sheet))[1])
    return blocks


def audit_theme(
    base_css: str, theme_css: str = "", theme_name: str = "tarmar"
) -> list[Pairing]:
    """Every text-on-background pairing a theme produces, worst ratio first.

    A component at rest and the same component under ``:hover`` are two
    pairings, reported under ``.x`` and ``.x:hover`` (issue #736). A state's
    text is its own ``color`` if the state rule sets one and the component's
    resting ink otherwise, which is why ``.button.danger:hover``'s red fill is
    measured against the white ``.button`` prints rather than against the
    page's ink.

    Args:
        base_css: The site-wide sheet.
        theme_css: The theme's override sheet, or "" for the default theme.
        theme_name: Name reported in each pairing.

    Returns:
        One :class:`Pairing` per painted component that can hold text, and one
        per state that paints a ground of its own.
    """
    painted = _painted(base_css, theme_css)
    grounds = sorted(painted.resting_backgrounds.items()) + sorted(
        painted.state_backgrounds.items()
    )

    pairings = []
    for selector, background in grounds:
        if _exempt(selector):
            continue
        foreground = (
            painted.ink_in_state(selector)
            if _is_layer_rule(selector)
            else painted.ink_for(selector)
        )
        pairings.append(
            Pairing(
                theme=theme_name,
                selector=selector,
                foreground=foreground,
                background=background,
                ratio=contrast_ratio(foreground, background),
                threshold=_threshold(selector),
            )
        )
    return sorted(pairings, key=lambda pairing: pairing.ratio)


def audit_text_on_ground(
    base_css: str, theme_css: str = "", theme_name: str = "tarmar"
) -> list[Pairing]:
    """Every pairing made by text that paints no ground of its own (issue #709).

    :func:`audit_theme` starts from the selectors that paint a background, so a
    rule that only sets ``color`` is never measured -- and the chrome is
    written exactly that way. ``.identity-since`` set ``#999`` and inherited
    the page's white; the footer set ``#888`` and did the same. Both were
    below AA on every page in the app, and the audit that exists to stop this
    class of defect reported nothing, because neither selector paints a box.

    This reads the other direction: for each selector that sets a text colour
    and no background, the ground is the nearest painted ancestor in the same
    sheets, falling back to ``body``'s background. ``footer a`` therefore
    measures against ``footer``'s ground if footer paints one and against the
    page ground if it does not, which is what a browser does here.

    "Paints no ground of its own" is read per layer (issue #736): a component
    that paints nothing at rest is measured here even when a ``:hover`` rule
    paints one, and a state that sets a colour but no ground of its own stands
    on the component's resting ground. Before that, one hover background was
    enough to take a component's resting text out of every audit there is.

    The same limits as the rest of this module apply -- a flat model of the
    cascade, opaque hex colours only -- and one more: a selector whose real
    ground comes from an ancestor these sheets do not name is measured against
    the page ground. That is the common case in the chrome and is why the
    check is worth having.

    Args:
        base_css: The site-wide sheet.
        theme_css: The theme's override sheet, or "" for the default theme.
        theme_name: Name reported in each pairing.

    Returns:
        One :class:`Pairing` per ground-inheriting text colour, worst first.
    """
    painted = _painted(base_css, theme_css)

    pairings = []
    inks = sorted(painted.resting_foregrounds.items()) + sorted(
        painted.state_foregrounds.items()
    )
    for selector, foreground in inks:
        stated = _is_layer_rule(selector)
        own_ground = (
            painted.state_backgrounds if stated else painted.resting_backgrounds
        )
        if selector in own_ground:
            continue  # audit_theme already measures a component that paints itself
        if _exempt(selector):
            continue
        # A state that paints no ground of its own stands on the component's
        # resting ground when there is one, and on the inherited ground
        # otherwise.
        background = painted.resting_backgrounds.get(
            _base_form(selector), painted.ground_under(selector)
        )
        threshold = _threshold(selector)
        pairings.append(
            Pairing(
                theme=theme_name,
                selector=selector,
                foreground=foreground,
                background=background,
                ratio=contrast_ratio(foreground, background),
                threshold=threshold,
            )
        )
    return sorted(pairings, key=lambda pairing: pairing.ratio)


def audit_conditionals(
    base_css: str, theme_css: str = "", theme_name: str = "tarmar"
) -> list[Pairing]:
    """Every pairing a conditional block *changes* (issues #681, #732).

    Each ``@media``/``@supports`` block is audited as an override layer on top
    of the unconditional sheets -- which is what it is when its condition
    holds -- and reported under its own name, so a failure says which block
    to look in.

    A layer reports only what its own block moves. Auditing a layer produces
    the whole sheet seen through it, so every unconditional pairing came back
    from every block: over the component sheets, six failing pairings read as
    138 across 23 blocks, and a caller could not tell six problems from a
    hundred and thirty-eight (issue #732). A pairing whose text and ground are
    both what the unconditional sheets already give it is that sheet's
    pairing, reported once by :func:`audit_theme`; this reports the rest --
    a component the block paints, and a component the block left alone while
    moving the ink underneath it, which is the #306 shape inside a block.

    Args:
        base_css: The site-wide sheet.
        theme_css: The theme's override sheet, or "" for the default theme.
        theme_name: Name the layers are reported under.

    Returns:
        One :class:`Pairing` per painted component whose colours a block
        changes, worst ratio first.
    """
    unconditional = base_css + "\n" + theme_css
    outside_any_block = {
        pairing.selector: (pairing.foreground, pairing.background)
        for pairing in audit_theme(unconditional, "", theme_name)
    }
    pairings: list[Pairing] = []
    for block in conditional_layers(base_css, theme_css):
        for pairing in audit_theme(
            unconditional, block.css, f"{theme_name} {block.condition}"
        ):
            colours = (pairing.foreground, pairing.background)
            if outside_any_block.get(pairing.selector) != colours:
                pairings.append(pairing)
    return sorted(pairings, key=lambda pairing: pairing.ratio)


def failures(
    base_css: str, theme_css: str = "", theme_name: str = "tarmar"
) -> list[Pairing]:
    """Every pairing below its threshold, conditional blocks included."""
    audited = (
        audit_theme(base_css, theme_css, theme_name)
        + audit_text_on_ground(base_css, theme_css, theme_name)
        + audit_conditionals(base_css, theme_css, theme_name)
    )
    return [pairing for pairing in audited if not pairing.passes]


_CUSTOM_PROPERTY_OR_HEX = re.compile(r"(--[\w-]+\s*:\s*[^;}]*)|(" + _HEX + ")")


def colour_literals(css: str, *, allow_custom_properties: bool) -> list[str]:
    """Hex colours a sheet paints with directly instead of through a token.

    Issue #338 item 11: base.css holds the palette (#312), and a component
    sheet that writes ``#666`` instead of ``var(--colour-ink-muted)`` is how a
    second, drifting palette grows. Comments are ignored -- they cite issue
    numbers like ``#312``, which read as colours.

    Args:
        css: The sheet's text.
        allow_custom_properties: True for a theme sheet, whose job is to define
            its own tokens (``--arena-accent: #443a86``); a literal anywhere
            else in it is still reported.

    Returns:
        Each offending literal, in source order.
    """
    found = []
    for block in re.findall(
        r"\{([^{}]*)\}", re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    ):
        for match in _CUSTOM_PROPERTY_OR_HEX.finditer(block):
            if match.group(1):
                if not allow_custom_properties:
                    found.extend(re.findall(_HEX, match.group(1)))
                continue
            found.append(match.group(2))
    return found


_CUSTOM_PROPERTY_DEFINITION = re.compile(r"(--[\w-]+)\s*:")
_CUSTOM_PROPERTY_USE = re.compile(r"var\(\s*(--[\w-]+)\s*(,)?")


def undefined_custom_properties(sheets: dict[str, str]) -> list[str]:
    """Each ``var(--x)`` with no fallback whose ``--x`` no sheet defines.

    Issue #461: ``.battle-deleted-note`` painted ``var(--battle-panel)``, a
    token nobody had defined. A ``var()`` naming an undefined property with no
    fallback is invalid at computed-value time, so the declaration silently
    renders as if it were never written -- valid CSS, no error, and a panel
    with no ground. A use that carries a fallback is deliberate and allowed.

    Args:
        sheets: Sheet name to its text, for every sheet the site loads; a
            token defined in any of them counts as defined.

    Returns:
        ``"<sheet>: --name"`` for each undefined use, in sheet then source order.
    """
    stripped = {
        name: re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
        for name, css in sheets.items()
    }
    defined = {
        match.group(1)
        for css in stripped.values()
        for match in _CUSTOM_PROPERTY_DEFINITION.finditer(css)
    }
    return [
        f"{name}: {match.group(1)}"
        for name, css in sorted(stripped.items())
        for match in _CUSTOM_PROPERTY_USE.finditer(css)
        if match.group(1) not in defined and not match.group(2)
    ]


def stylesheet(name: str) -> str:
    """Read a sheet from ``static/css`` by file name."""
    root = Path(__file__).resolve().parent.parent
    return (root / "static" / "css" / name).read_text()


def stylesheet_names() -> list[str]:
    """Every sheet in ``static/css``, in name order.

    The audit's callers have been naming sheets one at a time, which is how
    sixteen component sheets went unmeasured while the guard was green
    (issue #730). A caller that asks for the list gets every sheet the day it
    is added rather than the day somebody remembers to add it.
    """
    root = Path(__file__).resolve().parent.parent
    return sorted(path.name for path in (root / "static" / "css").glob("*.css"))


def custom_properties(css: str) -> dict[str, str]:
    """Every unconditional custom property a sheet defines, later winning.

    The palette lives in ``base.css`` as tokens (#312), so a test that wants
    to state what a named colour must clear needs to read the token rather
    than restate its value and drift from it.

    Args:
        css: A stylesheet's text.

    Returns:
        Property name to its declared value, ``--colour-ink`` to ``#333``.
    """
    return _variables(css)
