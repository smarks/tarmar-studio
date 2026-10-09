"""Project middleware."""

from collections.abc import Callable

from django.conf import settings
from django.contrib.auth import get_user
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils.functional import SimpleLazyObject
from speak_friend.middleware import PassphraseMiddleware

from common.gate import DEV_GATE_PASSPHRASE, normalize_passphrase
from common.helpers import client_ip
from common.public_paths import is_public_path
from common.rate_limit import (
    GUESS_MAX_ATTEMPTS,
    GUESS_WINDOW_SECONDS,
    charge,
    current_count,
    too_many_attempts,
)

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


class GateRateLimitMiddleware:
    """Rate-limit *failed* passphrase-gate attempts by client IP.

    The passphrase gate is a third-party middleware with no throttling, so a
    shared secret could otherwise be brute-forced unbounded. Only wrong guesses
    count toward the limit — correct submissions pass through freely — so normal
    use (and the e2e suite) is unaffected while brute-forcing is bounded.

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
    """

    MAX_ATTEMPTS = GUESS_MAX_ATTEMPTS
    WINDOW_SECONDS = GUESS_WINDOW_SECONDS

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.method == "POST" and "passphrase" in request.POST:
            identifier = client_ip(request)
            cache_key = f"rate_limit:gate:{identifier}"
            if current_count(cache_key) >= self.MAX_ATTEMPTS:
                return too_many_attempts(
                    "Too many wrong passphrases from this address.",
                    window_seconds=self.WINDOW_SECONDS,
                    request=request,
                    headline="The door has stopped answering.",
                )
            submitted = normalize_passphrase(request.POST.get("passphrase", ""))
            expected = normalize_passphrase(
                getattr(settings, "GATE_PASSPHRASE", DEV_GATE_PASSPHRASE)
            )
            if submitted != expected:
                charge(cache_key, self.WINDOW_SECONDS)
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
    parent stays the dependency it always was.
    """

    #: The session flag speak_friend sets once a visitor has answered the
    #: gate. The public branch has to preserve it by hand; the name lives in
    #: this module's GATE_SESSION_FLAG so nothing spells it twice.
    SESSION_FLAG = GATE_SESSION_FLAG

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
