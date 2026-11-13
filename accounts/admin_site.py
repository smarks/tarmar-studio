"""Django's admin site, with its sign-in held to the site's limiter (#173).

``django.contrib.admin.AdminSite.login`` is a plain ``LoginView`` with the
admin's form and template, and nothing bounds the guesses it takes. It
answers for the superuser accounts, behind a passphrase gate whose default is
public on purpose, so it needs the limit the site's own sign-in has had
since issue #160. ``accounts.apps.LimitedAdminConfig`` installs this class as
the default site, which is Django's one hook for it: ``admin.site`` resolves
to an instance of this class, so every ``@admin.register`` and the
``admin.site.urls`` mount are untouched.
"""

from typing import Any

from django.contrib.admin.sites import AdminSite
from django.contrib.auth.decorators import login_not_required
from django.http import HttpRequest, HttpResponse
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache

from accounts.login_limit import limit_failed_logins

#: The methods that only read the form: the ones Django's own CSRF check
#: treats as safe (``CsrfViewMiddleware.process_view`` in
#: ``django/middleware/csrf.py`` spells the tuple inline and exports no name
#: for it). Anything else is counted as a guess, so the guarantee does not
#: rest on Django leaving ``request.POST`` empty for a PUT or a PATCH (review
#: of #173).
UNCOUNTED_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


class LimitedAdminSite(AdminSite):
    """The default admin site, with ``login`` counted by the login limiter."""

    # The parent decorates its ``login`` with these two; an override does not
    # inherit a method's decorators, so they are applied again here.
    @method_decorator(never_cache)
    @login_not_required
    def login(
        self, request: HttpRequest, extra_context: dict[str, Any] | None = None
    ) -> HttpResponse:
        """Show the admin's sign-in form, or take a guess at it (issue #173).

        A safe method (:data:`UNCOUNTED_METHODS`) costs nothing; every other method is
        run through :func:`accounts.login_limit.limit_failed_logins`, the same
        limiter and the same per-address counter as ``/tarmar/accounts/login/``.

        Args:
            request: The request for the admin's sign-in page.
            extra_context: Passed through to the parent's view.

        Returns:
            The parent's response, or the limiter's 429.
        """
        if request.method in UNCOUNTED_METHODS:
            return super().login(request, extra_context)
        return limit_failed_logins(
            request, lambda: super(LimitedAdminSite, self).login(request, extra_context)
        )
