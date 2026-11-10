"""The one limiter on password guessing, for every sign-in form (#160, #173).

The site's own sign-in (``accounts.views.LoginView``) has bounded guessing
since issue #160: ten wrong passwords from one network address in five
minutes, the budget the passphrase gate also allows (``common.rate_limit``),
and only wrong passwords count. Django's admin mounts a second sign-in form
at ``/admin/login/``, for the accounts with the most power, and it had no
limit at all (issue #173). Both forms now come through here, so there is one
limiter, one set of thresholds and one counter per address: a guess at the
admin's door spends the same budget as a guess at the site's.

How the charge is taken is the #160 design, unchanged: the charge is taken
*before* the password is checked and refunded when the sign-in turns out to
be genuine. Charging afterwards would let N concurrent workers each read the
same count, spend the length of a password hash checking a guess, and only
then write back, so N workers each got the full allowance. A success does not
clear the counter: once an address has spent ten wrong passwords it waits out
the window, correct credentials included.

The words the 429 says are configuration, ``[login]`` in
``config/common-refusals.toml`` (``common.refusal_words``).
"""

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from common.helpers import client_ip
from common.rate_limit import (
    GUESS_MAX_ATTEMPTS,
    GUESS_WINDOW_SECONDS,
    charge,
    current_count,
    release,
    too_many_attempts,
)
from common.refusal_words import refusal_words

#: The counter key's prefix; the client's address completes it.
LOGIN_FAILURES_KEY_PREFIX = "rate_limit:login_failures:ip_"

FAILED_LOGIN_MAX_ATTEMPTS = GUESS_MAX_ATTEMPTS
FAILED_LOGIN_WINDOW_SECONDS = GUESS_WINDOW_SECONDS


def login_failures_key(client_address: str) -> str:
    """The counter key for sign-in failures from ``client_address``.

    Takes the address rather than the request so a test, or a browser spec
    reading it back through the Django shell, can name the key for the
    address it arrives from.

    Args:
        client_address: The client's address, as :func:`common.helpers.client_ip`
            resolves it.

    Returns:
        The ``RateLimitCounter`` key both sign-in forms charge.
    """
    return f"{LOGIN_FAILURES_KEY_PREFIX}{client_address}"


def limit_failed_logins(
    request: HttpRequest, attempt: Callable[[], HttpResponse]
) -> HttpResponse:
    """Run a sign-in request, counting it against the address only if it fails.

    Args:
        request: The sign-in request. Its ``user`` says afterwards whether the
            attempt signed somebody in.
        attempt: What answers the request when it is admitted: the form's own
            view. Called once, after the charge is taken.

    Returns:
        The 429 for an address that has spent its budget, else what
        ``attempt`` answered.
    """
    key = login_failures_key(client_ip(request))
    if current_count(key) >= FAILED_LOGIN_MAX_ATTEMPTS:
        return too_many_attempts(
            refusal_words().login.lockout_message,
            window_seconds=FAILED_LOGIN_WINDOW_SECONDS,
            headline=refusal_words().login.lockout_headline,
            request=request,
        )
    charge(key, FAILED_LOGIN_WINDOW_SECONDS)
    response = attempt()
    if request.user.is_authenticated:
        # A genuine sign-in is not a guess: refund the charge. release()
        # never touches expires_at, so this cannot extend the window; it only
        # removes the row once nothing is left charged in it, so a clean
        # sign-in leaves no trace of a window at all.
        release(key)
    # Else nothing further: charge() fixed this window's expiry on the first
    # wrong guess and leaves it alone on every one since, so a wrong password
    # never pushes the lockout's end further out (issue #161).
    return response
