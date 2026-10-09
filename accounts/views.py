"""Tarmar-studio's account views.

The auth logic (register/login/logout/profile/users-admin, plus the AJAX
profile-update and delete endpoints) now lives in the shared ``origami-auth``
library. These thin subclasses keep tarmar-studio's own templates, redirect
targets, and nav ``active_page`` values. The studio-only landing/home/about
pages stay local, and the two function-based endpoints are wrapped rather than
re-exported: the library draws its permission line at ``can_manage_users`` for
deletion, which leaves the Admin role reachable by accounts that cannot grant
it (issues #632 and #651). The guards live here, in front of the library's
views, because origami-auth is a separate repository on its own release
schedule and this site is the one that can be left with no Admin.
"""

from dataclasses import dataclass

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpRequest, HttpResponse
from django.urls import reverse, reverse_lazy
from django.views.decorators.http import require_POST
from django.views.generic import RedirectView, TemplateView
from origami_auth import views as auth_views
from origami_auth.views import ROLE_FIELDS
from origami_common.audit import audit_logger

# The library's own JSON error shapes, so a refusal drawn here is
# indistinguishable to the page from one drawn inside origami-auth. They are
# underscored but they are what every endpoint in that module returns. The
# body parser is common.helpers' instead: the library's lets invalid UTF-8
# and deep nesting raise (#767), and this one answers them with its 400.
from origami_common.helpers import _json_error, _permission_denied

from accounts.forms import RegistrationForm
from accounts.models import User
from battle.access import actor_of, listable_battles
from battle.models import Battle, BattleCombatant
from characters.models import Character
from common.breadcrumbs import BreadcrumbMixin
from common.helpers import _parse_json_body, client_ip, request_theme
from common.middleware import has_passed_gate
from common.mixins import ActivePageMixin
from common.rate_limit import (
    GUESS_MAX_ATTEMPTS,
    GUESS_WINDOW_SECONDS,
    charge,
    current_count,
    release,
    too_many_attempts,
)
from gm.models import GMSession


class FrontDoorView(RedirectView):
    """The root URL: send the visitor through their hostname's front door.

    On the default host that is the studio home, exactly as the old static
    redirect did; on a classic-arena host it is the battle list -- the arena
    IS the site there (issue #269). The choice is the theme's
    ``landing_url_name``, resolved through the same ``THEME_HOSTS`` mapping
    that skins the page, so a host can never wear one door's look and open
    onto the other's rooms. ``LOGIN_REDIRECT_URL``, logout, and registration
    all point here rather than at a hard-coded ``/tarmar/`` for the same
    reason.
    """

    permanent = False

    def get_redirect_url(self, *args, **kwargs):
        return reverse(request_theme(self.request).landing_url_name)


class RegisterView(auth_views.RegisterView):
    template_name = "accounts/register.html"
    # The shared form, with password refusals moved off Confirm Password and
    # onto the password they are about (issue #711).
    form_class = RegistrationForm
    # Registering signs you in, so land wherever this host's front door
    # leads (FrontDoorView routes per host).
    success_url = reverse_lazy("landing")

    def get_success_url(self) -> str:
        """The front door, unless it is one this visitor cannot open.

        Registration is public so the arena can offer an account at the moment
        a limit bites, and the same trap as :class:`LoginView` follows it: the
        front door is gated, so a brand-new account was answered with the
        passphrase form (issue #694). A visitor who has not passed the gate
        lands on the battle list instead.

        Returns:
            The URL to redirect to after a successful registration.
        """
        if not has_passed_gate(self.request):
            return reverse("battle:list")
        return str(self.success_url)


class LoginView(auth_views.LoginView):
    """Log in, counting only *failed* attempts against the brute-force limit.

    ``origami_auth.views.LoginView`` decorates its ``post`` with
    ``rate_limit("login", max_requests=10, window_seconds=300)``, which counts
    every POST — a signed-in-successfully one exactly like a wrong password. On
    a single-worker LocMemCache that was invisible: each gunicorn worker kept
    its own counter and every restart wiped them. Since issue #139 moved
    ``CACHES`` to ``DatabaseCache`` there is one counter for the whole site that
    survives restarts, so ten genuine logins from one address (an office, a
    household behind NAT, the serial e2e suite) now lock that address out of a
    site it was successfully using. Issue #160.

    A brute-force limiter exists to bound *guessing*, and a login that
    succeeded was not a guess, so only failures are charged here — the same
    rule ``common.middleware.GateRateLimitMiddleware`` already applies to the
    passphrase gate, and the same window and allowance it uses. A success does
    not clear the counter: once an address has spent ten wrong passwords it
    waits out the window, correct credentials included.

    The charge is taken *before* the password is checked and refunded when the
    login turns out to be genuine. Charging afterwards would mean every worker
    read the same count, spent the length of a password hash checking a guess,
    and only then wrote back — so N concurrent workers each get the full
    allowance. Spending first closes that window.

    The counter lives in ``common.rate_limit`` (a fixed-window count on
    ``RateLimitCounter``) rather than ``cache.incr``/``cache.decr``:
    ``DatabaseCache`` inherits ``BaseCache.incr()``, which is get-then-set in
    Python and not atomic under concurrent workers, and its window used to be
    rolling -- each wrong password re-touched the entry's expiry, pushing the
    lockout's end further out instead of ever letting it close (issue #161,
    the same fix ``common.middleware.GateRateLimitMiddleware`` needed).

    The library offers no hook for this, so the fix is the ordinary Django one:
    override ``post`` and skip exactly the library's decorated version of it.
    ``super(auth_views.LoginView, self)`` resumes the MRO one class past
    origami-auth's, landing on Django's own ``LoginView.post``.
    """

    template_name = "accounts/login.html"

    def get_success_url(self) -> str:
        """Where a completed sign-in lands, never behind a door it cannot open.

        ``LOGIN_REDIRECT_URL`` points at the front door, and on the studio host
        the front door is gated. So a visitor who came from the public arena,
        pressed the site menu's Login (which carries no ``?next=``) and typed
        the right password was answered with the passphrase form: no word that
        the sign-in had worked, no second secret explained, no way back
        (issue #694). ``common/public_paths`` names that exact trap in its own
        docstring and then walked into it one step later.

        An explicit ``?next=`` still wins -- the arena's own "Sign in" links
        carry one, and ``battle-sign-in-redirect.spec.js`` pins that. Without
        one, a visitor who has not answered the passphrase is sent to the
        battle list, which is public, instead of to a page that will stop them.

        Returns:
            The URL to redirect to after a successful sign-in.
        """
        explicit = self.get_redirect_url()
        if explicit:
            return explicit
        if not has_passed_gate(self.request):
            return reverse("battle:list")
        return super().get_success_url()

    FAILED_LOGIN_MAX_ATTEMPTS = GUESS_MAX_ATTEMPTS
    FAILED_LOGIN_WINDOW_SECONDS = GUESS_WINDOW_SECONDS

    def post(self, request, *args, **kwargs):
        cache_key = f"rate_limit:login_failures:ip_{client_ip(request)}"
        if current_count(cache_key) >= self.FAILED_LOGIN_MAX_ATTEMPTS:
            return too_many_attempts(
                "Too many failed sign-in attempts from this address. The "
                "count is per address, not per account, so a shared network "
                "can reach it without anyone guessing.",
                window_seconds=self.FAILED_LOGIN_WINDOW_SECONDS,
                request=request,
                headline="Too many sign-in attempts.",
            )

        charge(cache_key, self.FAILED_LOGIN_WINDOW_SECONDS)

        response = super(auth_views.LoginView, self).post(request, *args, **kwargs)

        if request.user.is_authenticated:
            # A genuine login is not a guess -- refund the charge. release()
            # never touches expires_at, so this cannot extend the window; it
            # only ever removes the row once nothing is left charged in it,
            # so a clean login leaves no trace of a window at all.
            release(cache_key)
        # else: nothing further to do. charge() fixed this window's expiry
        # on the first wrong guess and leaves it alone on every one since --
        # a wrong password no longer pushes the lockout's end further out.
        return response


class LogoutView(auth_views.LogoutView):
    # Back out through the front door you came in by: the root routes per
    # host, so the arena's visitors are not dumped on the studio home.
    next_page = "/"


class ProfileView(BreadcrumbMixin, auth_views.ProfileView):
    template_name = "accounts/profile.html"
    login_url = "login"
    breadcrumb_label = "Profile"


class UsersListView(BreadcrumbMixin, auth_views.UsersListView):
    template_name = "accounts/users_list.html"
    login_url = "login"
    breadcrumb_label = "Users"


class LandingView(TemplateView):
    template_name = "landing.html"


#: How much of each list the home page's "Your table" shows (issue #827); the
#: characters list and the Games Master's and battle pages hold the rest.
HOME_CHARACTER_LIMIT = 6
HOME_SESSION_LIMIT = 3
HOME_BATTLE_LIMIT = 3


@dataclass(frozen=True)
class PlayerTable:
    """What belongs to the signed-in player, as the home page lists it.

    Attributes:
        characters: Their own undeleted characters, living or dead, newest
            first, at most ``HOME_CHARACTER_LIMIT``.
        character_count: How many such characters they have in all.
        sessions: Their latest Games Master sessions.
        battles: The undeleted battles they made or hold a seat in, most
            recently changed first.
    """

    characters: list[Character]
    character_count: int
    sessions: list[GMSession]
    battles: list[Battle]


def player_table(request: HttpRequest) -> PlayerTable:
    """The signed-in player's characters, sessions and battles (issue #827).

    Every list is read from the database on each visit, so the page shows
    what is true now rather than what happened on the way in.

    Args:
        request: A request from a signed-in user.

    Returns:
        The player's table.
    """
    user = request.user
    # The owner rides along: each Play button asks gm.attach.attach_refusal,
    # which reads it, and one query per character is what this spares.
    own_characters = (
        Character.objects.filter(user=user, is_deleted=False)
        .select_related("user")
        .order_by("-created_at")
    )
    actor = actor_of(request)
    # The battle list's own scope for this viewer, narrowed to the battles
    # that are theirs: made by them, or holding one of their seats (the seat
    # rule the list's Play verb reads, ``Actor.seat_filter``).
    seated = BattleCombatant.objects.filter(actor.seat_filter()).values("battle_id")
    battles = (
        listable_battles(actor)
        .filter(Q(created_by=user) | Q(pk__in=seated))
        .order_by("-updated_at")
    )
    return PlayerTable(
        characters=list(own_characters[:HOME_CHARACTER_LIMIT]),
        character_count=own_characters.count(),
        sessions=list(GMSession.objects.filter(user=user)[:HOME_SESSION_LIMIT]),
        battles=list(battles[:HOME_BATTLE_LIMIT]),
    )


class HomeView(ActivePageMixin, TemplateView):
    template_name = "accounts/home.html"
    active_page = "home"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Signed out, the page is the public front page it always was.
        if self.request.user.is_authenticated:
            context["table"] = player_table(self.request)
        return context


class AboutView(BreadcrumbMixin, ActivePageMixin, TemplateView):
    breadcrumb_label = "About"
    template_name = "accounts/about.html"
    active_page = "about"


# --- User management endpoints ---------------------------------------------
#
# Both wrap origami-auth's view of the same name, add the guard it does not
# draw, and delegate. The library keeps every write, every audit line it
# already wrote, and its own rate limits; nothing below re-implements a
# permission ladder, so the two cannot drift apart into different answers for
# the same request.

ROLE_ADMIN_FIELD = "is_role_admin"

# How origami_auth.views.profile_update_field reads a posted role value. It
# exports no helper for this, so the one place that has to know what a write
# will do before it happens says so here, next to the reason.
ROLE_VALUES_MEANING_TRUE = (True, "true", "on", 1)


def _another_role_admin_exists(besides_pk: int | None) -> bool:
    """Is there a second account holding the Admin role?

    Args:
        besides_pk: The primary key of the account not to count. Optional
            only because a model instance's ``pk`` is; every account this is
            asked about has been read from the database.

    Returns:
        True when some other account can still grant and revoke roles.
    """
    return User.objects.filter(is_role_admin=True).exclude(pk=besides_pk).exists()


@login_required
@require_POST
def delete_user(request: HttpRequest, user_id: int) -> HttpResponse:
    """Delete a user, refusing anyone but a role admin an Admin account.

    Issue #632, live in production: the library checks ``can_manage_users``,
    which is ``is_role_admin or is_dm``. A DM can therefore neither tick nor
    untick anybody's Admin box, but could delete the account that owns it —
    and on a site with one role admin, which is this one's normal shape,
    nothing in the app can appoint a replacement afterwards. Deleting an
    account must not be a way around the check that editing it enforces, so
    the role line is drawn here as well, and a refusal is audited: an attempt
    to remove the site's only Admin is worth a record whether or not it
    succeeded.

    Args:
        request: The POST from the users list.
        user_id: The account to delete.

    Returns:
        The library's response, or a 403 when the target holds the Admin role
        and the caller does not.
    """
    if not request.user.can_manage_users:
        return _permission_denied()

    target_user = User.objects.filter(pk=user_id).first()
    if (
        target_user is not None
        and target_user.is_role_admin
        and not request.user.is_role_admin
    ):
        audit_logger.info(
            "USER_DELETE_REFUSED: user=%s tried to delete role admin user=%s (pk=%s)",
            request.user.username,
            target_user.username,
            target_user.pk,
        )
        return _permission_denied()

    return auth_views.delete_user(request, user_id)


@login_required
@require_POST
def profile_update_field(
    request: HttpRequest, user_id: int | None = None
) -> HttpResponse:
    """Update one profile field, keeping at least one Admin on the site.

    Issue #651: an admin's own profile renders the Admin checkbox, so unticking
    it cleared the flag on the acting account. The endpoint then refused every
    attempt to put it back and the checkbox was gone from the page; Django's
    ``/admin/`` is no way back either, since it gates on ``is_staff`` /
    ``is_superuser``, which nothing here sets. Recovery needed shell or
    database access on the deploy host. A write that would leave the site with
    no Admin at all is refused, wherever it is aimed, and the refusal says why
    so the page can show it rather than silently reverting.

    The library also audits role changes only when they are made to somebody
    else (``if user_id and old_value != new_value``), so a role change on your
    own account left no record; that line is written here.

    Every request that is not an admin's role write is handed straight to the
    library, which owns the permission ladder — including the lookup that
    answers 403 rather than 404 for an unknown user, so this wrapper cannot
    become a way to enumerate accounts.

    Args:
        request: The AJAX POST from a profile page.
        user_id: The account being edited, or None for the caller's own.

    Returns:
        The library's response, or a 400 naming the reason the write was
        refused.
    """
    data, parse_error = _parse_json_body(request)
    if parse_error:
        return parse_error
    field = data.get("field")
    # A list is unhashable, so ``in`` the library's set raised TypeError here
    # and again inside the library (#767); it names no field either way.
    if not isinstance(field, str):
        return _json_error("Invalid field")
    if field not in ROLE_FIELDS or not request.user.is_role_admin:
        return auth_views.profile_update_field(request, user_id)

    target_user = (
        request.user if user_id is None else User.objects.filter(pk=user_id).first()
    )
    if target_user is None:
        return auth_views.profile_update_field(request, user_id)

    new_value = data.get("value") in ROLE_VALUES_MEANING_TRUE
    old_value = getattr(target_user, field)
    if (
        field == ROLE_ADMIN_FIELD
        and not new_value
        and target_user.is_role_admin
        and not _another_role_admin_exists(target_user.pk)
    ):
        audit_logger.info(
            "ROLE_CHANGE_REFUSED: user=%s tried to clear %s on the last role "
            "admin user=%s (pk=%s)",
            request.user.username,
            field,
            target_user.username,
            target_user.pk,
        )
        return _json_error(
            "This is the only Admin account. Give another account the Admin "
            "role before taking it away here."
        )

    response = auth_views.profile_update_field(request, user_id)
    if user_id is None and old_value != new_value and response.status_code == 200:
        audit_logger.info(
            "ROLE_CHANGE: user=%s changed field=%s from %s to %s on their own "
            "account (pk=%s)",
            request.user.username,
            field,
            old_value,
            new_value,
            request.user.pk,
        )
    return response
