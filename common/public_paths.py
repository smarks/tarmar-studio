"""Which URLs the site-wide passphrase gate does NOT cover (issue #305).

The gate exists to keep crawlers and the idly curious off a work-in-progress
site. That is worth keeping for the parts of the site that are somebody's
work — characters, parties, the bestiary, encounters, ruins, the GM chat,
the rules reference, the admin. It is exactly wrong for the arena, which is
a game: a game nobody can reach without being told a password is a game
nobody plays.

So the split is by URL prefix, and this module is its single statement.
``common.middleware.PublicPathPassphraseMiddleware`` asks here; so does the
test that pins the split. Nothing else decides it.

Confirmed against ``tarmar_studio.urls``: every non-arena feature lives
under its own ``/tarmar/<app>/`` prefix, so exempting the arena's prefix
exempts the arena and nothing else. ``/`` is exact-matched rather than
prefixed — it is the front door, and on the classic-arena host
(``accounts.views.FrontDoorView``) the front door IS the battle list, so a
gated ``/`` would gate the arena by redirect.

The accounts prefixes are here because the arena's whole point is to offer
an account at the moment a limit bites ("sign in to save this"). A sign-in
link that lands on a passphrase form is not an offer. Note what this
implies and weigh it deliberately: **registration is reachable without the
passphrase**, so the gate is no longer an invite code for new accounts.
Everything an account unlocks stays gated regardless, and the login
throttle (``accounts.views.LoginView``) and gate throttle
(``common.middleware.GateRateLimitMiddleware``) are unaffected.
"""

from __future__ import annotations

#: The sign-in page's path. Settings' LOGIN_URL and LOGOUT_REDIRECT_URL use it,
#: and so do the deploy scripts, which cannot reverse URLs because they run
#: without Django.
LOGIN_PATH = "/tarmar/accounts/login/"

#: Path prefixes served without the passphrase gate.
PUBLIC_PATH_PREFIXES: tuple[str, ...] = (
    # The arena: list, staged setup, lobby, battle, seats, poll.
    "/tarmar/battles/",
    # Sign in / register / sign out, so the arena's "sign in to save"
    # affordances lead somewhere a guest can actually go.
    LOGIN_PATH,
    "/tarmar/accounts/logout/",
    "/tarmar/accounts/register/",
)

#: Paths served without the gate on an exact match only.
PUBLIC_PATHS_EXACT: tuple[str, ...] = ("/",)


def is_public_path(path: str) -> bool:
    """True when ``path`` is served without the passphrase gate."""
    return path in PUBLIC_PATHS_EXACT or path.startswith(PUBLIC_PATH_PREFIXES)
