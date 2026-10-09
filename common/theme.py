"""Host-driven presentation themes.

A theme decides what a page *looks like*, what it *calls itself*, which
room its front door opens onto, and which rules a battle opened at that door
*starts out* proposing. That last one is a default and nothing more (issue
#332): the door that says Melee opens a classic draft, because a visitor who
walks through it and clicks start should get the fight the sign promised,
not a six-phase Tarmar battle they never asked for. The dropdown is still
there and still decides.

What the theme still never does is decide the rules of a battle that already
exists. A battle's own ``rules_profile`` does that, and it is read from the
row, never from the hostname: a Tarmar battle viewed through the arena door
is still a Tarmar battle, and a classic battle viewed on the default host is
still classic. Keeping the theme out of every *resolved* rules decision is
what lets phase 5 put one deploy behind two front doors without the second
one quietly changing play.

The mapping from hostname to theme lives in settings (see ``THEME_HOSTS``),
built from an environment variable rather than a domain literal in this
module. The hostname a deployment answers to is deployment configuration:
hard-coding it here would mean this file could not be exercised in a browser
until DNS moved, and the cutover has not happened yet.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    """One presentation of the site.

    Attributes:
        key: Stable identifier, published to the page as ``data-theme`` so
            stylesheets and tests can name a theme without matching on copy.
        site_name: What the header and the default page title call the site.
        tagline: A short line under the wordmark; empty for no tagline.
        logo: Static path of the wordmark image, or ``None`` for a text-only
            wordmark. ``None`` is a real choice, not a gap -- inventing a mark
            for someone else's game is the one thing theming must not do.
        stylesheet: Static path of the sheet layered over ``base.css``, or
            ``None`` for the unthemed default.
        attribution: Footer line naming whose game the theme evokes; empty
            when there is nobody to credit.
        landing_url_name: URL name the front door opens onto -- where the
            root URL, and every login/logout/register without a ``next`` of
            its own, sends the visitor. The arena's door lands on the arena;
            the studio's lands on the studio home. Kept here so "which host
            is which" is decided in exactly one place (``THEME_HOSTS``) and
            never re-derived by parsing hostnames elsewhere (issue #269).
        nav: Which menu ``base.html`` renders -- ``"studio"`` for the full
            Tarmar nav, ``"arena"`` for the battles-first menu. A key rather
            than a boolean so a third front door needs a value, not a
            refactor.
        default_rules_profile: Name of the rules profile a *new* draft opened
            at this door starts on (issue #332). A plain string for the same
            reason ``landing_url_name`` is one: this module knows nothing of
            ``battle``, so it names the profile and lets ``battle`` resolve
            and validate it, the way Django resolves the URL name. It seeds
            a draft and never touches a battle that already exists.
    """

    key: str
    site_name: str
    tagline: str
    logo: str | None
    stylesheet: str | None
    attribution: str
    landing_url_name: str
    nav: str
    default_rules_profile: str


TARMAR_THEME = Theme(
    key="tarmar",
    site_name="Tarmar Studio",
    tagline="",
    logo="images/tarmar_gold.svg",
    stylesheet=None,
    attribution="",
    landing_url_name="home",
    nav="studio",
    default_rules_profile="tarmar",
)

# The classic arena. Restrained on purpose: a dark palette and a plain
# wordmark evoke the printed game without reproducing anyone's trade dress,
# and the attribution names the source rather than borrowing its look. The
# classic *rules data* stays quarantined in the engine's classic module, as
# it has since phase 5 was specified; nothing about that boundary moves
# because a page changed colour.
CLASSIC_ARENA_THEME = Theme(
    key="classic-arena",
    site_name="Melee",
    tagline="The classic arena",
    logo=None,
    stylesheet="css/theme-classic-arena.css",
    attribution=(
        "Melee and The Fantasy Trip are trademarks of Steve Jackson Games. "
        "This arena is an independent implementation and is not affiliated "
        "with or endorsed by Steve Jackson Games."
    ),
    landing_url_name="battle:list",
    nav="arena",
    default_rules_profile="classic-melee",
)

THEMES = {theme.key: theme for theme in (TARMAR_THEME, CLASSIC_ARENA_THEME)}

DEFAULT_THEME = TARMAR_THEME


def normalize_host(host: str) -> str:
    """Reduce a ``Host`` header to the form ``THEME_HOSTS`` is keyed by.

    Strips the port and lowercases, because neither distinguishes one front
    door from another: ``melee.localhost:8000`` and ``MELEE.LOCALHOST`` are
    the same door as ``melee.localhost``. IPv6 literals keep their brackets
    -- the port, if any, follows the closing bracket.
    """
    host = host.strip().lower()
    if host.startswith("["):
        closing = host.find("]")
        if closing != -1:
            return host[: closing + 1]
        return host
    return host.split(":", 1)[0]


def theme_for_host(host: str, host_themes: dict[str, str]) -> Theme:
    """Return the theme a hostname selects, or the default for any other.

    An unknown theme *key* in the mapping falls back to the default rather
    than raising: the mapping is built from an environment variable, and a
    typo in deployment config should serve the ordinary site, not 500 every
    page on it.
    """
    key = host_themes.get(normalize_host(host))
    if key is None:
        return DEFAULT_THEME
    return THEMES.get(key, DEFAULT_THEME)
