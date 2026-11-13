"""Project middleware."""

from collections.abc import Callable, Sequence

from django.conf import settings
from django.contrib.auth import get_user
from django.core.exceptions import ImproperlyConfigured, MiddlewareNotUsed
from django.http import HttpRequest, HttpResponse, HttpResponseBadRequest
from django.middleware.csrf import CsrfViewMiddleware
from django.shortcuts import render
from django.utils.functional import SimpleLazyObject
from speak_friend.middleware import PassphraseMiddleware

from common.gate import (
    CSP_MIDDLEWARE,
    DEV_GATE_PASSPHRASE,
    FRAME_OPTIONS_MIDDLEWARE,
    GATE_LIMITER_MIDDLEWARE,
    GATE_MIDDLEWARE,
    GATE_PATH,
    choose_gate_hint,
    normalize_passphrase,
)
from common.helpers import client_ip
from common.public_paths import is_public_path
from common.rate_limit import (
    GUESS_MAX_ATTEMPTS,
    GUESS_WINDOW_SECONDS,
    charge,
    current_count,
    too_many_attempts,
)
from common.refusal_words import refusal_words
from common.rolling import pinned_dice

#: The session flag speak_friend sets once a visitor has answered the gate.
#: Spelled once here, and read by PublicPathPassphraseMiddleware below and by
#: :func:`has_passed_gate`.
GATE_SESSION_FLAG = "passphrase_authenticated"


def has_passed_gate(request: HttpRequest) -> bool:
    """Has this visitor already answered the site-wide passphrase?

    A view that is itself public needs this to know whether the page it is
    about to send someone to is one they can reach (issue #694).

    Args:
        request: The request whose session carries the gate flag.

    Returns:
        True when the passphrase has been answered in this session.
    """
    return bool(request.session.get(GATE_SESSION_FLAG, False))


class RenderedMethodNotAllowedMiddleware:
    """Give a 405 a page, the way 403, 404 and 500 already have one.

    Django's ``require_POST`` and ``View.http_method_not_allowed`` answer with
    an ``HttpResponseNotAllowed``, whose body is empty -- so a GET to
    ``/tarmar/battles/setup/`` or ``/tarmar/gm/bestiary/generate/``, which is
    what a pasted link or a reload produces, drew a completely blank page
    (#716, low finding). Django offers ``handler403``/``handler404``/
    ``handler500`` and no ``handler405``, so this is the hook: only a 405 that
    carries no body of its own is replaced, and the ``Allow`` header the
    response already set is carried across so nothing downstream changes.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        if response.status_code != 405 or getattr(response, "streaming", False):
            return response
        if response.content:
            return response  # a view that drew its own page keeps it
        allowed = response.headers.get("Allow", "")
        rendered = render(request, "405.html", {"allowed_methods": allowed}, status=405)
        if allowed:
            rendered["Allow"] = allowed
        return rendered


#: The request header a browser test sets to pin one request's dice (#959).
PINNED_DICE_HEADER = "X-E2E-Dice-Seed"


class PinnedDiceMiddleware:
    """Roll a request's dice from the seed its ``X-E2E-Dice-Seed`` header names.

    The browser tests' stand-in for ``patch(roll_survival_dice)`` (issue
    #959): a spec that clicks a roll button cannot patch the server, so it
    names a seed on that one request and asserts the faces the seed gives.
    The pin is per request, not per process, because four Playwright workers
    share one dev server and a process-wide seed would hand each of them
    whatever was left of one interleaved stream.

    Test-only, three times over. ``settings.py`` installs it only when
    ``E2E_DICE_SEEDING`` is on and refuses to load with that switch when
    ``DEBUG`` is off; and should it be installed some other way, it removes
    itself at startup unless both are true. A request without the header is
    untouched; a header that is not a whole number is refused with a 400, so
    a spec that misspells its seed fails instead of rolling real dice. The
    pin ends when the view returns, so a streamed body's dice are not pinned.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        if not (settings.DEBUG and getattr(settings, "E2E_DICE_SEEDING", False)):
            raise MiddlewareNotUsed("dice seeding is for DEBUG browser tests only")
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        named_seed = request.headers.get(PINNED_DICE_HEADER)
        if named_seed is None:
            return self.get_response(request)
        try:
            seed = int(named_seed)
        except ValueError:
            return HttpResponseBadRequest(
                f"{PINNED_DICE_HEADER} must be a whole number, not {named_seed!r}",
                content_type="text/plain",
            )
        with pinned_dice(seed):
            return self.get_response(request)


class TrustedProxyClientIPMiddleware:
    """Rewrite ``REMOTE_ADDR`` to the real client IP behind a trusted proxy.

    ``origami_common.decorators.rate_limit`` — the login and AJAX throttle
    shared across the origami apps — reads ``request.META["REMOTE_ADDR"]``
    directly and offers no hook for anything else. Behind nginx that is the
    proxy's address for every visitor, so all anonymous callers share a single
    bucket. Rewriting META at the outer edge of the stack is the standard Django
    answer: downstream code keeps reading the attribute it already reads, and
    the value it finds is finally the caller's.

    The rewrite defers entirely to :func:`common.helpers.client_ip`, so it
    inherits every guard documented there: the ``TRUST_PROXY_IP_HEADER`` flag,
    the ``TRUSTED_PROXY_IPS`` peer check that stops something else on this box
    from forging an identity, and the requirement that the forwarded value
    actually parse as an IP address. When any of those declines, META is left
    exactly as it arrived. When the rewrite does happen, the proxy's own address
    stays recoverable as ``REMOTE_ADDR_PROXY``.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if getattr(settings, "TRUST_PROXY_IP_HEADER", False):
            original = request.META.get("REMOTE_ADDR", "")
            resolved = client_ip(request)
            if resolved != original:
                request.META["REMOTE_ADDR_PROXY"] = original
                request.META["REMOTE_ADDR"] = resolved
        return self.get_response(request)


#: The gate counter key's prefix; the client's address completes it.
GATE_COUNTER_KEY_PREFIX = "rate_limit:gate:"


def gate_counter_key(client_address: str) -> str:
    """The counter key for wrong passphrases from ``client_address``.

    Takes the address rather than the request so a test, or a browser spec
    reading it back through the Django shell, can name the key for the
    address it arrives from.

    Args:
        client_address: The client's address, as :func:`common.helpers.client_ip`
            resolves it.

    Returns:
        The ``RateLimitCounter`` key ``GateRateLimitMiddleware`` charges.
    """
    return f"{GATE_COUNTER_KEY_PREFIX}{client_address}"


def _answered_in_middleware(request: HttpRequest) -> HttpResponse:
    """Stand in for the view a request answered in middleware does not have.

    ``CsrfViewMiddleware.process_view`` only reads its callback's attributes
    (``csrf_exempt``); it never calls it. Should that ever change, this fails
    loudly rather than answering anything.
    """
    raise RuntimeError("the gate's POST is answered in middleware, never by a view")


def csrf_rejection(
    request: HttpRequest, get_response: Callable[[HttpRequest], HttpResponse]
) -> HttpResponse | None:
    """Django's CSRF verdict on a request a middleware is about to answer itself.

    ``CsrfViewMiddleware`` checks the token in ``process_view``, which Django
    calls only once a request has reached the URL resolver. A request a
    middleware answers on its own -- the gate's POST, which has no view --
    never gets there, so the token ``gate.html`` rendered was never read, and
    a cross-site form could post to the gate (issue #175). This puts the same
    question to the same middleware at the point the request is about to be
    answered. Everything ``process_view`` honours still holds: the test
    client's ``enforce_csrf_checks`` switch, the referer check on a secure
    request, ``CSRF_TRUSTED_ORIGINS``, and the cookie the outer
    ``CsrfViewMiddleware`` already read into ``request.META``.

    Args:
        request: The POST about to be answered.
        get_response: The next handler, which the middleware needs to exist
            and never calls here.

    Returns:
        Django's 403 for a missing or wrong token, or ``None`` when the
        request may go on.
    """
    return CsrfViewMiddleware(get_response).process_view(
        request, _answered_in_middleware, (), {}
    )


def require_gate_middleware_order(middleware: Sequence[str]) -> None:
    """Refuse a ``MIDDLEWARE`` that puts the gate's parts in the wrong order.

    The gate answers requests itself, so what stands outside it in
    ``settings.MIDDLEWARE`` decides what its own responses carry and what
    reaches it: the Content-Security-Policy and clickjacking headers are added
    only by a middleware outside it (issues #178, #176), and the limiter in
    front of it is what checks the POST's CSRF token and counts the guess
    (issue #175). All are facts about the list, so they are checked once, at
    startup, instead of being trusted.

    Args:
        middleware: ``settings.MIDDLEWARE``, or a list under test.

    Raises:
        ImproperlyConfigured: When one of the four is missing or out of order.
    """
    expected_order = (
        CSP_MIDDLEWARE,
        FRAME_OPTIONS_MIDDLEWARE,
        GATE_LIMITER_MIDDLEWARE,
        GATE_MIDDLEWARE,
    )
    try:
        positions = [middleware.index(name) for name in expected_order]
    except ValueError as missing:
        raise ImproperlyConfigured(
            f"MIDDLEWARE must list each of {expected_order}; the passphrase gate "
            "needs all of them (issues #175, #176, #178)."
        ) from missing
    if positions != sorted(positions):
        raise ImproperlyConfigured(
            "MIDDLEWARE must list the gate's parts in this order: "
            f"{', '.join(expected_order)} (issues #175, #176, #178)."
        )


class GateRateLimitMiddleware:
    """Admit, then count, the POSTs to the passphrase gate (issues #161, #175).

    The passphrase gate is a third-party middleware with no throttling, so a
    shared secret could otherwise be brute-forced unbounded. Only wrong guesses
    count toward the limit — correct submissions pass through freely — so normal
    use (and the e2e suite) is unaffected while brute-forcing is bounded.

    Only a POST to the gate's own path, ``common.gate.GATE_PATH``, is a guess.
    It used to be any POST carrying a ``passphrase`` field, so ten such posts
    to a public page locked an address out of the gate without a single
    passphrase ever being compared (issue #175). And before the guess is
    counted, Django's CSRF check is run on it (:func:`csrf_rejection`): the
    gate's POST has no view, so ``CsrfViewMiddleware`` never checked the
    token the form carried. A POST refused there is not a guess -- its
    passphrase is never read, by this middleware or the gate -- so it is
    not charged; charging it would hand a hostile page the very lockout the
    check exists to stop. The check lives here, in front of the gate, rather
    than in both middlewares: ``PublicPathPassphraseMiddleware`` refuses to
    start unless this one precedes it (:func:`require_gate_middleware_order`).

    "Correct" here must be classified exactly the way
    speak_friend.PassphraseMiddleware classifies it, or this middleware and
    the gate disagree about which attempts are wrong (see common/gate.py and
    issue #138) -- this used to exempt attempts from the counter that the
    gate itself would reject. Both sides normalize with the shared
    normalize_passphrase() to keep that in sync.

    The counter itself lives in ``common.rate_limit`` (a fixed-window count
    on ``RateLimitCounter``, atomic at the SQL level) rather than
    ``cache.get``/``cache.set``: the cache-based version read the count and
    wrote it back in Python, which is not atomic under concurrent workers,
    and ``cache.set(key, attempts + 1, WINDOW_SECONDS)`` pushed the entry's
    expiry forward on every wrong guess -- a rolling window that kept
    extending a lockout instead of ever letting it close (issue #161).

    The same counter also decides when the gate offers a hint (issue #55; the
    evidence is in ``common/gate.py``). Once a client's wrong guesses in the
    window reach ``settings.GATE_HINT_AFTER_WRONG_GUESSES`` a hint rides along
    with the refusal, drawn at random by ``choose_gate_hint()`` for each
    response, so a visitor who keeps guessing is shown the riddle from another
    side each time (issue #77). The hints are configuration, not code:
    ``config/gate-hints.txt``, loaded into ``settings.GATE_HINTS`` at startup,
    and the threshold is ``settings.GATE_HINT_AFTER_WRONG_GUESSES``, so both
    can change with no edit here. No new state is kept for the hint: the count
    ``charge()`` returns is the one already being kept for the lockout, and
    which hint was drawn is not remembered. The
    hint is attached to the request as ``request.gate_hint`` rather than
    rendered here, so speak_friend's own wrong-answer ``render(request,
    "gate.html", ...)`` stays the one thing that draws the page and
    ``gate.html`` reads the attribute through the ``request`` context
    processor. It is set per response to a wrong POST: a GET of the gate never
    reads the counter, so a reload shows no hint until the next wrong guess.
    The 429 for a spent budget carries the hint too, since by then the count
    is past the threshold by definition.
    """

    MAX_ATTEMPTS = GUESS_MAX_ATTEMPTS
    WINDOW_SECONDS = GUESS_WINDOW_SECONDS

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.method == "POST" and request.path == GATE_PATH:
            rejection = csrf_rejection(request, self.get_response)
            if rejection is not None:
                return rejection
            cache_key = gate_counter_key(client_ip(request))
            if current_count(cache_key) >= self.MAX_ATTEMPTS:
                # A count at MAX_ATTEMPTS is already past
                # settings.GATE_HINT_AFTER_WRONG_GUESSES, so the hint goes on
                # the 429 without a second look at the threshold (issue #55).
                return too_many_attempts(
                    refusal_words().gate.lockout_message,
                    window_seconds=self.WINDOW_SECONDS,
                    headline=refusal_words().gate.lockout_headline,
                    request=request,
                    hint=choose_gate_hint(),
                )
            submitted = normalize_passphrase(request.POST.get("passphrase", ""))
            expected = normalize_passphrase(
                getattr(settings, "GATE_PASSPHRASE", DEV_GATE_PASSPHRASE)
            )
            if submitted != expected:
                wrong_guesses_this_window = charge(cache_key, self.WINDOW_SECONDS)
                if wrong_guesses_this_window >= settings.GATE_HINT_AFTER_WRONG_GUESSES:
                    request.gate_hint = choose_gate_hint()
        return self.get_response(request)


class PublicPathPassphraseMiddleware(PassphraseMiddleware):
    """The passphrase gate, with the arena carved out of it (issue #305).

    ``speak_friend.PassphraseMiddleware`` gates every path but its own
    hard-coded ``EXEMPT_PATHS`` (``/health/``), and offers no hook for an
    app to add to that list. Rather than fork the dependency or fight it
    with a second middleware, this subclass answers first for the paths
    ``common.public_paths`` calls public and delegates everything else —
    including the ``/gate/`` POST and the session bookkeeping — to the
    parent, untouched. The gate's behavior for gated paths is therefore
    still entirely speak_friend's.

    Registered in place of the parent in ``settings.MIDDLEWARE``; the
    parent stays the dependency it always was. It refuses to start unless
    ``GateRateLimitMiddleware`` (the CSRF check and the count, issue #175)
    and ``XFrameOptionsMiddleware`` (the header on its own pages, issue
    #176) are listed before it: see :func:`require_gate_middleware_order`.
    """

    #: The session flag speak_friend sets once a visitor has answered the
    #: gate. The public branch has to preserve it by hand; the name lives in
    #: this module's GATE_SESSION_FLAG so nothing spells it twice.
    SESSION_FLAG = GATE_SESSION_FLAG

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        super().__init__(get_response)
        require_gate_middleware_order(settings.MIDDLEWARE)

    def __call__(self, request):
        if not is_public_path(request.path):
            self._offer_the_signed_in_account(request)
            return super().__call__(request)
        # A public path still has to carry the parent's one side effect:
        # ``login()`` and ``logout()`` cycle or flush the session, which
        # drops the gate flag, and speak_friend puts it back afterwards.
        # Sign-in and sign-out are public paths now, so without this a
        # visitor who had already passed the gate would be thrown back to
        # it the moment they logged out.
        was_authenticated = request.session.get(self.SESSION_FLAG, False)
        response = self.get_response(request)
        if was_authenticated and not request.session.get(self.SESSION_FLAG, False):
            request.session[self.SESSION_FLAG] = True
        return response

    @staticmethod
    def _offer_the_signed_in_account(request: HttpRequest) -> None:
        """Put ``request.user`` in place before the gate template renders.

        This middleware runs ahead of ``AuthenticationMiddleware`` on purpose
        -- an unauthenticated visitor must meet the passphrase before any view
        runs -- so ``request.user`` does not exist yet, and ``gate.html`` saw
        an AnonymousUser even for somebody who had just signed in. That is
        what let the gate stare back at a visitor with no acknowledgement
        (issue #694).

        The session already holds everything needed to answer "who is this?",
        so the account is resolved here the same lazy way
        ``AuthenticationMiddleware`` resolves it, and only for a gated path,
        where the gate template is the one thing that may render.
        ``AuthenticationMiddleware`` overwrites the attribute for every
        request that gets past the gate, so nothing downstream sees a
        difference.

        Args:
            request: The request about to be handed to the gate.
        """
        if not hasattr(request, "user"):
            request.user = SimpleLazyObject(lambda: get_user(request))
