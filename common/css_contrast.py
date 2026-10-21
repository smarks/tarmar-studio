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

``opacity`` is read as a fade (issue #49). A rule that sets ``opacity`` below 1
fades the element and everything inside it towards whatever stands behind it,
so text that passed at full strength can fail once faded: the downed combatant
card's text fell from about 5.4:1 to 2.3:1 under ``opacity: 0.55`` and nothing
failed. A colour ``c`` in a faded element is measured as ``backdrop + (c -
backdrop) * opacity``, where the backdrop is the ground behind the element:
the nearest painted ancestor's own, unfaded ground, else the page's. Nested
opacities compose from the inside out, each level blending against its own
backdrop and then being faded by every level around it, which is how a
browser composites nested groups (an inner group lands on the outer element's
ground, and the outer opacity then fades the whole result once). A rule at
``opacity: 0`` is invisible and passes. A selector that fades but sets neither a ground nor a text colour of
its own, like ``.combatant-card.is-down``, is measured too: it shows the
ink and ground of the component it modifies. The opacity of a selector is the
nearest rule that names it, its ``.a.b`` falling back to ``.a``, the same way
its ink is found.

A modifier carries its component's descendants onto whatever it paints (issue
#91). ``.equipment-row .item-equipped`` is written for the row, applies inside
``.equipment-row.in-pack.pack-dropped`` as well, and there stands on the
dropped row's grey, not on the row's white. So every descendant rule of a
component is measured again inside each modifier of it that paints a ground
or fades -- a compound with a class more (``.row.dropped``) or a state
(``.row:hover``) -- under the modifier's name and on the modifier's ground.
An ancestor named in a selector is read the same way: ``.row:hover .name``
stands on the hover ground, and ``.row.dropped .name`` on the row's ground when
the modifier paints none of its own. A rule that clears its ground
(``background: none`` or ``transparent``) paints nothing, and a modifier
that clears it does not borrow the component's ground.

The walk from a modifier to its component is as flat as the rest. It drops
trailing classes from the last compound only: ``.row.in-pack.dropped`` that
paints nothing tries ``.row.in-pack`` and then ``.row``, never ``.row.dropped``,
and source order between those rules is not read. The modifiers of a
component are found the same way, so a modifier written with its classes in
another order (``.dropped.row``) is not seen as one. A descendant's ink is its
own rule's colour or the page's: colour inherited from a modifier that sets
only ``color`` is not carried down to the text inside it. No live sheet relies
on any of these today.

Only opaque ``#rgb``/``#rrggbb`` colours participate. ``transparent``,
``currentColor``, gradients and ``rgba()`` are skipped rather than guessed at:
a wrong guess here would either mask a real failure or invent a false one.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field
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
# An opacity value: a number, or a percentage, with or without !important.
_OPACITY = re.compile(r"(\d*\.?\d+)(%?)(?:\s*!\s*important)?", re.IGNORECASE)
# The property that fades an element and all it holds. ``fill-opacity`` and
# ``stroke-opacity`` fade one SVG paint and are not read.
OPACITY_PROPERTY = "opacity"
# A ground a rule clears rather than paints: the element shows what is behind
# it, so the ground a modifier inherits from its component stops here.
# ``background-color: none`` is invalid CSS a browser ignores, so only the
# shorthand may say ``none``.
_CLEARED_GROUND = {
    "background": re.compile(r"(?:none|transparent)(?:\s*!\s*important)?", re.I),
    "background-color": re.compile(r"transparent(?:\s*!\s*important)?", re.I),
}
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
    channels = [channel / 255 for channel in _channels(colour)]
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


def _opacity(value: str) -> float | None:
    """The opacity a declaration sets, 0 to 1, or None if it is not a plain one."""
    found = _OPACITY.fullmatch(value.strip())
    if found is None:
        return None
    amount = float(found.group(1))
    return min(amount / 100 if found.group(2) else amount, 1.0)


def _blend(colour: str, backdrop: str, opacity: float) -> str:
    """``colour`` shown at ``opacity`` over ``backdrop``, as an opaque ``#rrggbb``.

    ``backdrop + (colour - backdrop) * opacity`` per channel, which is what a
    browser paints for an element faded to ``opacity`` (issue #49).
    """
    top, behind = _channels(colour), _channels(backdrop)
    mixed = (
        int(under + (over - under) * opacity + 0.5)  # half up, as a browser rounds
        for over, under in zip(top, behind)
    )
    return "#" + "".join(f"{channel:02x}" for channel in mixed)


def _channels(colour: str) -> tuple[int, int, int]:
    """The red, green and blue of an opaque ``#rgb`` or ``#rrggbb`` colour."""
    digits = colour.lstrip("#")
    if len(digits) == 3:
        digits = "".join(character * 2 for character in digits)
    return (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16))


@dataclass(frozen=True)
class Pairing:
    """One text-on-background pairing the sheets produce, with its ratio.

    ``foreground`` and ``background`` are what the sheets declare. ``ratio`` is
    measured on what a reader sees, which differs from them only when
    ``opacity`` (the product of every fade on the element and its ancestors)
    is below 1.
    """

    theme: str
    selector: str
    foreground: str
    background: str
    ratio: float
    threshold: float
    opacity: float = 1.0

    @property
    def passes(self) -> bool:
        """Whether the text is readable; fully transparent text has nothing to read."""
        return self.opacity == 0 or self.ratio >= self.threshold

    def __str__(self) -> str:
        faded = f" faded to opacity {self.opacity:g}" if self.opacity < 1 else ""
        return (
            f"{self.theme}: {self.selector} -- {self.foreground} on "
            f"{self.background}{faded} is {self.ratio:.2f}:1, "
            f"needs {self.threshold}:1"
        )


# Selector tokens that join two compounds rather than being one.
_COMBINATORS = frozenset({">", "+", "~"})

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


def _ancestors(selector: str, modified_by: tuple[int, str] | None = None) -> list[str]:
    """The ancestors a selector names, outermost first, states kept.

    ``.row:hover .cell .name`` names ``.row:hover`` and ``.row:hover .cell``;
    a combinator is not an ancestor. ``modified_by`` puts a modifier in place
    of the first ``depth`` parts, so ``.row .name`` read inside
    ``.row.dropped`` names ``.row.dropped`` (issue #91).

    Args:
        selector: The text's selector, resting or stated.
        modified_by: ``(how many parts the modifier replaces, the modifier)``.

    Returns:
        Each ancestor's prefix of the selector, as :func:`_stated_form`
        spells it.
    """
    parts = _stated_form(selector).split()
    ancestors = []
    for depth in range(1, len(parts)):
        if parts[depth - 1] in _COMBINATORS:
            continue
        if modified_by and modified_by[0] == depth:
            ancestors.append(modified_by[1])
        else:
            ancestors.append(" ".join(parts[:depth]))
    return ancestors


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
        resting_opacities: Component to the opacity it sets at rest (issue #49).
        state_opacities: ``".x:hover"`` to the opacity that state sets.
        resting_cleared: Components whose ground a rule clears, with
            ``background: none`` or ``transparent``: they paint nothing, and
            a modifier of theirs does not borrow its component's ground.
        state_cleared: ``".x:hover"`` selectors that clear their ground.
    """

    resting_backgrounds: dict[str, str]
    resting_foregrounds: dict[str, str]
    state_backgrounds: dict[str, str]
    state_foregrounds: dict[str, str]
    resting_opacities: dict[str, float] = field(default_factory=dict)
    state_opacities: dict[str, float] = field(default_factory=dict)
    resting_cleared: set[str] = field(default_factory=set)
    state_cleared: set[str] = field(default_factory=set)

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

    def opacity_for(self, component: str) -> float:
        """The opacity a component at rest renders at, following its base rules.

        Args:
            component: A resting selector, as :func:`_base_form` spells it.

        Returns:
            The nearest opacity the sheets set for it, else 1.
        """
        for candidate in _generalisations(component):
            if candidate in self.resting_opacities:
                return self.resting_opacities[candidate]
        return 1.0

    def opacity_in_state(self, stated: str) -> float:
        """The opacity a component renders at in one state.

        A state that sets no opacity of its own stays as faded as the
        component is at rest.

        Args:
            stated: A state selector, as :func:`_stated_form` spells it.

        Returns:
            The state's own opacity if it sets one, else the resting opacity.
        """
        if stated in self.state_opacities:
            return self.state_opacities[stated]
        return self.opacity_for(_base_form(stated))

    def sets_ink(self, selector: str) -> bool:
        """Whether a rule written for exactly this selector sets a text colour."""
        foregrounds = (
            self.state_foregrounds
            if _is_layer_rule(selector)
            else self.resting_foregrounds
        )
        return selector in foregrounds

    def ground_of(self, component: str) -> str | None:
        """The ground a component paints, following its base rules, or None.

        ``.combatant-card.is-down`` is the card, so it stands on the ground
        ``.combatant-card`` paints even though no rule gives it one -- unless
        the modifier clears it (``background: none``), which stops the walk.
        """
        return self.own_ground(component)

    def own_ground(self, selector: str, fallback: str | None = None) -> str | None:
        """The ground a selector's element paints itself, or None.

        A state's own ground first, then the component's resting ground
        through its base rules (:meth:`ground_of`). The first rule that says
        anything about the ground decides: a colour is the answer, and a
        cleared ground (``background: none``) is None, so a quiet button
        does not stand on the loud one's fill (issue #91).

        Args:
            selector: A selector, resting or stated.
            fallback: A less specific rule for the same element, read only
                when nothing for ``selector`` mentions the ground:
                ``.row .weight`` behind ``.row.dropped .weight``.

        Returns:
            An opaque colour, or None when the element paints nothing.
        """
        for candidates in (selector, fallback):
            if candidates is None:
                continue
            for backgrounds, cleared, candidate in self._ground_rules(candidates):
                if candidate in backgrounds:
                    return backgrounds[candidate]
                if candidate in cleared:
                    return None
        return None

    def _ground_rules(self, selector: str):
        """Where a selector's ground may be set, most specific first."""
        if _is_layer_rule(selector):
            yield self.state_backgrounds, self.state_cleared, selector
            selector = _base_form(selector)
        for candidate in _generalisations(selector):
            yield self.resting_backgrounds, self.resting_cleared, candidate

    def fade(
        self,
        selector: str,
        foreground: str,
        background: str,
        paints_ground: bool,
        modified_by: tuple[int, str] | None = None,
        leaf_opacity: float | None = None,
    ) -> tuple[str, str, str, float]:
        """The colours a reader sees once every fade on a selector is applied.

        Each element fades what it holds towards what stands behind it
        (``backdrop + (colour - backdrop) * opacity``). A colour in nested
        faded elements is blended from the inside out: first against the
        unfaded ground its own element stands on (the nearest painted
        ancestor's, else the page's), then faded again by each element around
        it against that element's backdrop. A ground is faded only by the
        element that paints it and the elements around that one, never by
        elements inside it (issue #49).

        Args:
            selector: The text's selector, resting or stated.
            foreground: The text colour the sheets give it.
            background: The ground the sheets give it.
            paints_ground: Whether that ground is the selector's own, and so
                fades with it, rather than an ancestor's, which fades with
                the ancestor.
            modified_by: A modifier standing in for one of the selector's
                ancestors, as ``(how many parts of the selector it replaces,
                the modifier)``: text inside ``.card`` seen as text inside
                ``.card.is-down``, which fades it, or inside ``.card:hover``,
                which repaints it. Its opacity and ground replace that
                ancestor's (issues #49, #91).
            leaf_opacity: The selector's own opacity, when a more specific
                rule than the selector sets it; else read from the sheets.

        Returns:
            The foreground and background as seen, the unfaded ground the text
            stands on, and the product of every opacity on the way. When
            nothing fades: the inputs unchanged, and 1.0.
        """
        if leaf_opacity is None:
            leaf_opacity = (
                self.opacity_in_state(selector)
                if _is_layer_rule(selector)
                else self.opacity_for(selector)
            )
        ancestors = [
            self.ancestor_paint(ancestor)
            for ancestor in _ancestors(selector, modified_by)
        ]
        total = leaf_opacity
        for _, opacity in ancestors:
            total *= opacity
        if total == 1.0:
            return foreground, background, background, 1.0

        surface = self.page_ground
        surface_level = -1  # index of the element that paints it; -1 is the page
        backdrops: list[tuple[str, float]] = []
        for level, (ground, opacity) in enumerate(ancestors):
            backdrops.append((surface, opacity))
            if ground:
                surface, surface_level = ground, level
        backdrops.append((surface, leaf_opacity))

        def seen(colour: str, innermost_level: int) -> str:
            for backdrop, opacity in reversed(backdrops[: innermost_level + 1]):
                colour = _blend(colour, backdrop, opacity)
            return colour

        leaf_level = len(backdrops) - 1
        if paints_ground:
            stands_on, background_level = background, leaf_level
        else:
            stands_on, background_level = surface, surface_level
        return (
            seen(foreground, leaf_level),
            seen(stands_on, background_level),
            stands_on,
            total,
        )

    def ancestor_paint(self, ancestor: str) -> tuple[str | None, float]:
        """The ground an ancestor named in a selector paints, and its opacity.

        An ancestor in a state (``.row:hover`` in ``.row:hover .name``)
        paints its state ground if it has one and its resting ground
        otherwise. A modifier (``.row.dropped``) paints its own ground if it
        has one and the component's otherwise, the way :meth:`ground_of`
        reads it (issue #91).

        Args:
            ancestor: A prefix of a selector, as :func:`_stated_form` spells it.

        Returns:
            The ground, or None when nothing the ancestor is paints one, and
            the opacity it renders at.
        """
        if _is_layer_rule(ancestor):
            return self.own_ground(ancestor), self.opacity_in_state(ancestor)
        return self.own_ground(ancestor), self.opacity_for(ancestor)

    def ground_under(
        self, selector: str, modified_by: tuple[int, str] | None = None
    ) -> str:
        """The ground a text colour lands on when it paints none of its own.

        The nearest ancestor named in the selector that paints a ground, read
        by :meth:`ancestor_paint`, falling back to the page's. A state's
        ground is its own if it paints one, else the component's resting
        ground, else this.

        Args:
            selector: The text's selector, resting or stated.
            modified_by: A modifier standing in for one of its ancestors, as
                :meth:`fade` takes it.

        Returns:
            An opaque ``#rrggbb`` colour.
        """
        for ancestor in reversed(_ancestors(selector, modified_by)):
            ground, _ = self.ancestor_paint(ancestor)
            if ground:
                return ground
        return self.page_ground


@functools.lru_cache(maxsize=64)
def _painted(base_css: str, theme_css: str) -> PaintedColours:
    """Read every colour the sheets paint, resting rules apart from state rules.

    Remembered per pair of sheets: every audit reads the same sheets, and
    :func:`audit_conditionals` reads them again under each block, so parsing
    them once a pair is most of what the whole audit costs (issue #91). The
    maps are never written to once built.

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
        cleared = painted.state_cleared if stated else painted.resting_cleared
        # In the order the rule writes them: the later of ``background`` and
        # ``background-color`` decides the ground, as it does in a browser.
        for property_name, written in declarations.items():
            clears = _CLEARED_GROUND.get(property_name)
            if clears is not None:
                value = _resolve(written, variables)
                colour = _colour(value)
                if colour:
                    backgrounds[key] = colour
                    cleared.discard(key)
                elif clears.fullmatch(value.strip()):
                    backgrounds.pop(key, None)
                    cleared.add(key)
        if "color" in declarations:
            colour = _colour(_resolve(declarations["color"], variables))
            if colour:
                foregrounds[key] = colour
        if OPACITY_PROPERTY in declarations:
            opacity = _opacity(_resolve(declarations[OPACITY_PROPERTY], variables))
            if opacity is not None:
                opacities = (
                    painted.state_opacities if stated else painted.resting_opacities
                )
                opacities[key] = opacity
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
    """Property to value for one rule body, last declaration winning.

    Each property sits where its last declaration is written, so a caller
    reading two properties that set the same thing (``background`` and
    ``background-color``) meets them in the order that decides between them.
    """
    result: dict[str, str] = {}
    for piece in block.split(";"):
        if ":" not in piece:
            continue
        name, _, value = piece.partition(":")
        name = name.strip().lower()
        if name.startswith("--") or not name:
            continue
        result.pop(name, None)
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
            _pairing(
                painted,
                theme_name,
                selector,
                foreground,
                background,
                paints_ground=True,
            )
        )
    return sorted(pairings, key=lambda pairing: pairing.ratio)


def _pairing(
    painted: PaintedColours,
    theme_name: str,
    selector: str,
    foreground: str,
    background: str,
    *,
    paints_ground: bool,
    modified_by: tuple[int, str] | None = None,
    reported_as: str | None = None,
    leaf_opacity: float | None = None,
) -> Pairing:
    """One pairing, measured on what a reader sees after every fade (issue #49).

    ``reported_as`` names the pairing when it differs from the selector that is
    measured: a descendant seen inside a modifier (``modified_by``).
    """
    seen_foreground, seen_background, stands_on, opacity = painted.fade(
        selector, foreground, background, paints_ground, modified_by, leaf_opacity
    )
    return Pairing(
        theme=theme_name,
        selector=reported_as or selector,
        foreground=foreground,
        background=stands_on,
        ratio=contrast_ratio(seen_foreground, seen_background),
        threshold=_threshold(selector),
        opacity=opacity,
    )


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
        # otherwise; a modifier that sets only a colour stands on its
        # component's ground unless it clears it.
        component_ground = painted.own_ground(selector)
        pairings.append(
            _pairing(
                painted,
                theme_name,
                selector,
                foreground,
                component_ground or painted.ground_under(selector),
                paints_ground=component_ground is not None,
            )
        )
    return sorted(pairings, key=lambda pairing: pairing.ratio)


def audit_faded(
    base_css: str, theme_css: str = "", theme_name: str = "tarmar"
) -> list[Pairing]:
    """Every pairing of a selector that only fades, and sets no colour (issue #49).

    :func:`audit_theme` starts from a ground and :func:`audit_text_on_ground`
    from a text colour, so ``.combatant-card.is-down { opacity: 0.55 }`` -- a
    modifier that changes neither, and fades the card it modifies -- is
    reached by neither. It is read here as the component it modifies: the ink
    and ground of the nearest rule that names it, seen through its own fade.
    A selector that does set a ground or a colour is already measured, fade
    and all, by those two.

    The text a faded modifier holds is the rest of the failure: the rename
    note on the downed card, not the card. That is measured by
    :func:`audit_modified_descendants`, with the text inside every other
    modifier.

    Args:
        base_css: The site-wide sheet.
        theme_css: The theme's override sheet, or "" for the default theme.
        theme_name: Name reported in each pairing.

    Returns:
        One :class:`Pairing` per selector that sets only an opacity below 1,
        worst first.
    """
    painted = _painted(base_css, theme_css)
    pairings = []
    faded = sorted(
        [(selector, False) for selector in painted.resting_opacities]
        + [(selector, True) for selector in painted.state_opacities]
    )
    for selector, stated in faded:
        measured_elsewhere = (
            painted.state_backgrounds | painted.state_foregrounds
            if stated
            else painted.resting_backgrounds | painted.resting_foregrounds
        )
        opacity = (
            painted.state_opacities[selector]
            if stated
            else painted.resting_opacities[selector]
        )
        if opacity >= 1 or selector in measured_elsewhere or _exempt(selector):
            continue
        foreground = (
            painted.ink_in_state(selector) if stated else painted.ink_for(selector)
        )
        component_ground = painted.own_ground(selector)
        pairings.append(
            _pairing(
                painted,
                theme_name,
                selector,
                foreground,
                component_ground or painted.ground_under(selector),
                paints_ground=component_ground is not None,
            )
        )
    return sorted(pairings, key=lambda pairing: pairing.ratio)


def audit_modified_descendants(
    base_css: str, theme_css: str = "", theme_name: str = "tarmar"
) -> list[Pairing]:
    """Every descendant rule of a component, seen inside each of its modifiers.

    A rule written for the component (``.equipment-row .item-equipped``)
    applies inside every modifier of it too (``.equipment-row.in-pack.
    pack-dropped``), and there it stands on what the modifier paints. A
    modifier that fades takes the text with it (issue #49); a modifier that
    repaints the ground moves the ground out from under it, and the audit
    measured the descendant on the component's ground only, so the equipped
    label's 4.50:1 on the dropped row's grey was looked at by nobody (issue
    #91). Each descendant is measured again under the modifier's name
    (``.equipment-row.in-pack.pack-dropped .item-equipped``), on the ground
    the modifier paints and through its fade, and the ground it was measured
    on is the one reported.

    A modifier is a rule that paints a ground or fades: a compound with a
    class more than its component (``.row.dropped`` modifies ``.row``), or a
    state of the component (``.row:hover`` modifies ``.row``). A descendant
    the sheets also write under the modifier's own name with a text colour of
    its own is that rule's to measure, since its colour wins in a browser. One
    that sets only a ground or an opacity under that name still shows the
    component's ink, so it is measured here with that rule's ground and
    opacity in place of the component's.

    Args:
        base_css: The site-wide sheet.
        theme_css: The theme's override sheet, or "" for the default theme.
        theme_name: Name reported in each pairing.

    Returns:
        One :class:`Pairing` per descendant rule and modifier of its
        component, worst first.
    """
    painted = _painted(base_css, theme_css)
    named = (
        set(painted.resting_backgrounds)
        | set(painted.resting_foregrounds)
        | set(painted.resting_opacities)
        | set(painted.state_backgrounds)
        | set(painted.state_foregrounds)
        | set(painted.state_opacities)
    )
    descendants_of: dict[str, list[str]] = {}
    for descendant in sorted(named):
        parts = descendant.split()
        for depth in range(1, len(parts)):
            descendants_of.setdefault(" ".join(parts[:depth]), []).append(descendant)

    pairings: dict[str, Pairing] = {}
    for modifier, components in _modifiers(painted):
        for component in components:
            depth = len(component.split())
            for descendant in descendants_of.get(component, []):
                parts = descendant.split()
                reported_as = " ".join([modifier, *parts[depth:]])
                if painted.sets_ink(reported_as) or _exempt(descendant):
                    continue
                pairing = _descendant_pairing(
                    painted, theme_name, descendant, (depth, modifier), reported_as
                )
                pairings.setdefault(str(pairing), pairing)
    return sorted(pairings.values(), key=lambda pairing: pairing.ratio)


def _modifiers(painted: PaintedColours) -> list[tuple[str, list[str]]]:
    """Each rule that paints a ground or fades, with the components it modifies.

    ``.row.in-pack.dropped`` modifies ``.row.in-pack`` and ``.row``;
    ``.row.dropped:hover`` modifies ``.row.dropped`` and ``.row``. A
    pseudo-element is a box of its own and holds no descendants, and an
    exempt rule (a disabled control) is not measured.
    """
    resting = set(painted.resting_backgrounds) | {
        selector
        for selector, opacity in painted.resting_opacities.items()
        if opacity < 1
    }
    stated = set(painted.state_backgrounds) | {
        selector for selector, opacity in painted.state_opacities.items() if opacity < 1
    }
    found = []
    for modifier in sorted(resting):
        components = list(_generalisations(modifier))[1:]
        if components and not _exempt(modifier):
            found.append((modifier, components))
    for modifier in sorted(stated):
        if _PSEUDO_ELEMENT.search(modifier) or _exempt(modifier):
            continue
        found.append((modifier, list(_generalisations(_base_form(modifier)))))
    return found


def _descendant_pairing(
    painted: PaintedColours,
    theme_name: str,
    descendant: str,
    modified_by: tuple[int, str],
    reported_as: str,
) -> Pairing:
    """One descendant rule measured inside a modifier standing in for an ancestor.

    A rule the sheets write under the reported name (``.row.dropped .weight``)
    that sets a ground or an opacity, and no colour, supplies those in place
    of the descendant's own.
    """
    stated = _is_layer_rule(descendant)
    own_ground = painted.own_ground(reported_as, fallback=descendant)
    own_opacities = (
        painted.state_opacities
        if _is_layer_rule(reported_as)
        else painted.resting_opacities
    )
    return _pairing(
        painted,
        theme_name,
        descendant,
        painted.ink_in_state(descendant) if stated else painted.ink_for(descendant),
        own_ground or painted.ground_under(descendant, modified_by),
        paints_ground=own_ground is not None,
        modified_by=modified_by,
        reported_as=reported_as,
        leaf_opacity=own_opacities.get(reported_as),
    )


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
    moving the ink underneath it, which is the #306 shape inside a block. Text
    that paints no ground of its own and a fade are read in the layers too: a
    block that only recolours or only fades a ``color`` rule paints no ground,
    and was missed until it was read this way (issue #49).

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
        pairing.selector: (pairing.foreground, pairing.background, pairing.opacity)
        for pairing in audit_theme(unconditional, "", theme_name)
        + audit_text_on_ground(unconditional, "", theme_name)
        + audit_faded(unconditional, "", theme_name)
        + audit_modified_descendants(unconditional, "", theme_name)
    }
    pairings: list[Pairing] = []
    for block in conditional_layers(base_css, theme_css):
        layer_name = f"{theme_name} {block.condition}"
        for pairing in (
            audit_theme(unconditional, block.css, layer_name)
            + audit_text_on_ground(unconditional, block.css, layer_name)
            + audit_faded(unconditional, block.css, layer_name)
            + audit_modified_descendants(unconditional, block.css, layer_name)
        ):
            colours = (pairing.foreground, pairing.background, pairing.opacity)
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
        + audit_faded(base_css, theme_css, theme_name)
        + audit_modified_descendants(base_css, theme_css, theme_name)
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
