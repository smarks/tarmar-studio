"""The decorators every app's views share.

``rate_limit`` is a re-export: the implementation lives once in
``origami_common.decorators``, and this module keeps the
``common.decorators.rate_limit`` import path working for the views across
tarmar-studio that already use it. :func:`internal_errors_logged` is this
project's own (issues #113, #139).
"""

import functools
import logging
from collections.abc import Callable

from django.core.exceptions import BadRequest, PermissionDenied, SuspiciousOperation
from django.http import Http404, HttpRequest, HttpResponseBase
from django.http.multipartparser import MultiPartParserError
from origami_common.decorators import rate_limit

from common.helpers import _json_error, wants_json
from common.refusal_words import refusal_words

__all__ = ["DJANGO_ANSWERED_EXCEPTIONS", "internal_errors_logged", "rate_limit"]

#: The exceptions Django answers itself, each with a 4xx of its own
#: (``django.core.handlers.exception.response_for_exception``): a page that
#: is not there, a permission refused, a request it cannot read. None is the
#: server failing, so :func:`internal_errors_logged` lets them through.
DJANGO_ANSWERED_EXCEPTIONS = (
    Http404,
    PermissionDenied,
    MultiPartParserError,
    BadRequest,
    SuspiciousOperation,
)


def internal_errors_logged(
    *, json_only: bool = False
) -> Callable[[Callable[..., HttpResponseBase]], Callable[..., HttpResponseBase]]:
    """Keep an internal failure's text off the page (issues #13, #39, #113, #139).

    A view answers the refusals it knows -- a rule, a malformed request -- in
    their own words. Anything else it raises -- a ``ValueError`` from an
    internal check, a ``RuntimeError``, ``KeyError`` or ``TypeError`` from a
    service -- is the server failing, and its text is not the player's to
    read. A script caller gets ``refusal_words().decorators.request_failed``
    as JSON with status 500, the error logged with its traceback under the
    view's own module's logger, so the page's script always has an answer it
    can parse. A form post gets the error raised on, so Django logs it once
    with its traceback and renders the site's own error page. What Django
    answers itself (:data:`DJANGO_ANSWERED_EXCEPTIONS`, a 404 above all) goes
    on to Django either way.

    Args:
        json_only: The endpoint answers only JSON, so its error is JSON even
            for a post with no JSON body (a bodyless ``postWithCsrf``).

    Returns:
        The decorator.
    """

    def decorate(
        view: Callable[..., HttpResponseBase],
    ) -> Callable[..., HttpResponseBase]:
        logger = logging.getLogger(view.__module__)

        @functools.wraps(view)
        def guarded(request: HttpRequest, *args, **kwargs) -> HttpResponseBase:
            try:
                return view(request, *args, **kwargs)
            except DJANGO_ANSWERED_EXCEPTIONS:
                raise
            except Exception:  # noqa: BLE001 — logged with its traceback, never shown
                if not (json_only or wants_json(request)):
                    raise
                logger.exception("Endpoint %s failed", request.path)
                return _json_error(
                    refusal_words().decorators.request_failed, status=500
                )

        return guarded

    return decorate
