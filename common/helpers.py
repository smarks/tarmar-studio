"""Request/response helpers.

The generic JSON helpers live in ``origami_common.helpers`` and are
re-exported here, except the body parser: ``origami_common``'s catches only
``JSONDecodeError``, so a body of invalid UTF-8 or deep nesting raised out of
every JSON endpoint (issue #767). The guarded parser and the number readers
below are the one path request data takes here until the package catches
the same cases. ``_can_edit_object`` is tarmar-studio-specific (it reaches
into the domain object's ``user`` ownership) so it stays local.
"""

import hmac
import ipaddress
import json
from typing import Any

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.utils.http import url_has_allowed_host_and_scheme
from origami_common.helpers import (
    _json_error,
    _permission_denied,
    _require_field,
)

from common.theme import Theme, theme_for_host

__all__ = [
    "_json_error",
    "_permission_denied",
    "_parse_json_body",
    "_require_field",
    "parse_json_text",
    "whole_number",
    "database_integer",
    "MAX_DATABASE_INTEGER",
    "_can_edit_object",
    "is_owner_or_manager",
    "client_ip",
    "request_theme",
    "safe_next_url",
    "UNKNOWN_CLIENT_IP",
]

UNKNOWN_CLIENT_IP = "unknown"

#: The largest value a SQLite INTEGER column holds. A larger Python int in a
#: query's ``IN`` list raises ``OverflowError`` from the driver (issue #767).
MAX_DATABASE_INTEGER = 2**63 - 1


def parse_json_text(text: bytes | str) -> object:
    """``json.loads`` for text a user sent, with every refusal a ``ValueError``.

    ``json.loads`` raises ``JSONDecodeError`` for malformed text, but also
    ``UnicodeDecodeError`` for bytes that are not UTF-8, ``ValueError`` for
    an integer longer than Python's digit limit, and ``RecursionError`` for
    deep nesting (``"[" * 100000``). The first three are ``ValueError``s;
    the last is not, so it is turned into one here.

    Args:
        text: The request body or uploaded file's bytes, or decoded text.

    Returns:
        The parsed JSON value.

    Raises:
        ValueError: The text is not JSON this server can read.
    """
    try:
        return json.loads(text)
    except RecursionError:
        raise ValueError("The JSON is nested too deeply") from None


def _parse_json_body(request: HttpRequest) -> tuple[dict, JsonResponse | None]:
    """Parse a JSON object request body.

    Same contract as ``origami_common.helpers._parse_json_body``: returns
    ``(data, None)`` on success or ``({}, error_response)`` on failure, an
    empty dict rather than None on error so callers (which always check the
    error first) get a non-Optional ``data``. Unlike it, every body a user
    can send that is not a JSON object answers 400 "Invalid JSON" (issue
    #767); see :func:`parse_json_text`.

    Args:
        request: The request whose body to read.
    """
    try:
        data = parse_json_text(request.body)
    except ValueError:
        return {}, _json_error("Invalid JSON")
    if not isinstance(data, dict):
        return {}, _json_error("Invalid JSON")
    return data, None


def whole_number(value: Any) -> int:
    """``int(value)`` for a value off a request, every refusal a ``ValueError``.

    What ``int`` accepts is unchanged (``"3"``, ``3``, ``2.9`` truncating to
    2). What it refuses with something other than ``ValueError`` is folded
    into one: ``OverflowError`` for infinity, which a JSON body reaches by
    sending ``1e999``, and ``TypeError`` for a list, dict or null. Callers
    that caught ``(ValueError, TypeError)`` missed the first (issue #767).

    Args:
        value: Whatever the request carried.

    Returns:
        The integer.

    Raises:
        ValueError: ``int`` could not make a whole number of it.
    """
    try:
        return int(value)
    except (OverflowError, TypeError) as error:
        raise ValueError("Not a whole number") from error


def database_integer(value: Any) -> int:
    """A primary key or sequence number off a request, as an int SQLite holds.

    Accepts an ``int`` (not a ``bool``) or a string of ASCII digits, the two
    shapes a client sends an id in. ``str.isdigit`` also accepts "²", which
    ``int`` then refuses, and ``int`` accepts other scripts' digits, so the
    string must be ASCII decimal. A value outside SQLite's INTEGER range is
    refused too: filtering ``pk__in`` on one raises ``OverflowError`` from
    the driver (issue #767).

    Args:
        value: Whatever the request carried.

    Returns:
        The integer.

    Raises:
        ValueError: The value is not an id this database could hold.
    """
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise ValueError("Not a whole number")
    if isinstance(value, str) and not (value.isascii() and value.isdecimal()):
        raise ValueError("Not a whole number")
    number = int(value)
    if not -MAX_DATABASE_INTEGER - 1 <= number <= MAX_DATABASE_INTEGER:
        raise ValueError("Out of range")
    return number


def request_theme(request: HttpRequest) -> Theme:
    """The presentation theme this request's hostname selects.

    The one sanctioned way to ask "which front door is this?" outside a
    template: it goes through the same ``THEME_HOSTS`` mapping every other
    consumer uses, so host classification is decided in exactly one place.
    ``common.theme`` itself stays free of Django settings so its own tests
    need no configuration; this wrapper is where settings come in.
    """
    return theme_for_host(request.get_host(), settings.THEME_HOSTS)


def is_owner_or_manager(user, owner) -> bool:
    """The one statement of ownership power: the owner, or a DM/admin.

    Characters (owned through ``user``) and parties (owned through ``owner``)
    both grant editing on this rule; each used to spell it out separately.

    Args:
        user: The acting user.
        owner: The user who owns the object in question.

    Returns:
        True when ``user`` is ``owner`` or may manage other users' content.
    """
    return owner == user or user.can_manage_users


def _can_edit_object(request, obj):
    """Check if the user can edit an object they own or via admin/DM privileges."""
    return is_owner_or_manager(request.user, obj.user)


def _parsed_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse an IP address, or ``None`` if the text is not one.

    Everything that comes out of a header goes through here. An unparsed value
    would end up as a cache key (DatabaseCache's ``cache_key`` is varchar(255),
    so a long one raises rather than throttles) and in the access log, where a
    crafted string is log injection. Parsing also normalises the text, so one
    client cannot hold several buckets by varying the spelling of its address.
    """
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def _peer_is_trusted_proxy(peer: str) -> bool:
    """Is the socket peer one of the proxies whose headers we believe?

    ``TRUST_PROXY_IP_HEADER`` alone is not enough. It says "our proxy sets these
    headers", not "this particular connection came through it", and gunicorn
    binds 127.0.0.1 on a box that also hosts other apps. Without this check any
    process that can open a socket to the port could name itself whatever it
    liked and take over another visitor's rate-limit bucket.

    The set is ``settings.TRUSTED_PROXY_IPS``, which settings.py always defines;
    the fallback here is empty rather than a second copy of the default, so a
    project that somehow omits it trusts nobody instead of guessing. Addresses
    are compared parsed, so ``::1`` still matches its expanded spelling.
    """
    peer_address = _parsed_ip(peer)
    if peer_address is None:
        return False
    trusted = getattr(settings, "TRUSTED_PROXY_IPS", ())
    return any(_parsed_ip(candidate) == peer_address for candidate in trusted)


def _proxy_secret_satisfied(request: HttpRequest) -> bool:
    """Does this request carry the secret only nginx is meant to know?

    ``TRUSTED_PROXY_IPS`` can only ask "did this connection's socket peer
    match a trusted address?" -- and on this deployment nginx and every
    other process on the same box (melee, orge) all present the same
    loopback peer, so that check alone cannot tell nginx's proxying apart
    from another local process forging ``X-Real-IP`` (issue #161 part 3).

    ``settings.PROXY_SHARED_SECRET`` closes that gap: nginx injects it as a
    header on every request it proxies (see ``nginx/tarmar.conf`` and
    ``nginx/tarmar-proxy-secret.conf.example``), from a value that lives in
    nginx's own root-owned config, unreadable by the unprivileged user the
    Django apps on this box run as. A local process can still open a
    loopback socket, but it cannot read that file, so it cannot supply the
    header.

    Unset (empty) by default, in which case this returns ``True``
    unconditionally -- the secret is opt-in infrastructure, not yet
    provisioned for every deployment (or for dev/test, which have no nginx
    in front of them at all), and the peer-IP check above is what already
    guards this path when it is absent. Comparison is constant-time: a
    shared secret is exactly the kind of value a timing side-channel on
    ``==`` could leak one byte at a time.

    Both sides are encoded to UTF-8 bytes before comparing.
    ``hmac.compare_digest`` raises ``TypeError`` if given a ``str`` with
    non-ASCII characters, and WSGI decodes header bytes as latin-1 -- so a
    header value with any high byte would otherwise crash this check with
    an unhandled exception instead of just failing it. That header is
    exactly the thing the co-hosted adversary this function exists to stop
    can set to whatever it likes, so it has to be handled, not just
    unlikely.
    """
    configured = getattr(settings, "PROXY_SHARED_SECRET", "")
    if not configured:
        return True
    provided = request.META.get("HTTP_X_PROXY_SHARED_SECRET", "")
    return hmac.compare_digest(provided.encode("utf-8"), configured.encode("utf-8"))


def _forwarded_client_ip(request: HttpRequest) -> str | None:
    """The client address the proxy reported, or ``None`` if it reported none."""
    candidates = [request.META.get("HTTP_X_REAL_IP", "")]
    hops = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")
    candidates.append(hops[-1])
    for candidate in candidates:
        address = _parsed_ip(candidate)
        if address is not None:
            return str(address)
    return None


def client_ip(request: HttpRequest) -> str:
    """Return the requesting client's IP address, honouring the trust model.

    Everything that rate-limits per client has to agree on what "a client" is,
    so this is the one place that decides.

    ``REMOTE_ADDR`` is the socket peer Django itself saw. When the app is
    reachable directly it is the only trustworthy value: forwarding headers are
    then just strings the caller typed, and honouring them would let one
    attacker spend everyone else's quota — or evade their own — by rotating a
    header. So REMOTE_ADDR is the default and the fallback.

    Behind the production reverse proxy the opposite problem appears:
    REMOTE_ADDR is nginx, identical for every visitor, which collapses every
    per-client bucket into one site-wide bucket (issue #139). Setting
    ``TRUST_PROXY_IP_HEADER`` (env var of the same name) says "our proxy sets
    these headers" and switches the lookup to them. Only set it when that is
    literally true; on a directly reachable port it hands clients control of
    their own identity.

    That flag is a statement about the deployment, not about the connection in
    hand, so it is not sufficient on its own. The headers are believed only when
    the socket peer is in ``TRUSTED_PROXY_IPS`` (default: loopback), when
    ``PROXY_SHARED_SECRET`` -- if configured -- arrived correctly (closing the
    gap the peer check alone cannot: another process on the same host presents
    that same loopback peer too, see :func:`_proxy_secret_satisfied`), and only
    when the value they carry parses as an IP address — see
    :func:`_peer_is_trusted_proxy` and :func:`_parsed_ip` for why each check has
    to be there. Anything else falls back to REMOTE_ADDR.

    ``nginx/tarmar.conf`` sends both of these::

        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

    ``X-Real-IP`` is written from nginx's own ``$remote_addr``, replacing
    anything the client sent, so it is the peer address nginx actually accepted
    and is preferred here. ``$proxy_add_x_forwarded_for`` *appends* that peer to
    whatever ``X-Forwarded-For`` arrived, so on this deployment the left-most
    entry is attacker-supplied and the right-most is the real client — hence the
    fallback reads the last hop, not the first.
    """
    peer = request.META.get("REMOTE_ADDR") or ""
    if (
        getattr(settings, "TRUST_PROXY_IP_HEADER", False)
        and _peer_is_trusted_proxy(peer)
        and _proxy_secret_satisfied(request)
    ):
        forwarded = _forwarded_client_ip(request)
        if forwarded is not None:
            return forwarded
    return peer or UNKNOWN_CLIENT_IP


def safe_next_url(request: HttpRequest, parameter: str = "next") -> str:
    """A same-site continuation URL from the request, or empty string.

    The battle setup's round trip (#270) sends the DM through the character
    editor with ``?next=<setup page>``; anything a browser could be tricked
    into supplying is filtered here — only a relative same-host URL comes
    back, everything else is dropped rather than followed.
    """
    candidate = request.POST.get(parameter) or request.GET.get(parameter) or ""
    if url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return ""
