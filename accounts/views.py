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
from typing import Any, cast

from django.contrib.auth import views as django_auth_views
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.db.models import Q
from django.forms import BaseForm
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.urls import reverse, reverse_lazy
from django.views.decorators.http import require_POST
from django.views.generic import FormView, RedirectView, TemplateView
from django.views.generic.base import ContextMixin
from origami_auth import views as auth_views
from origami_auth.views import ROLE_FIELDS
from origami_common.audit import audit_logger
from origami_common.decorators import rate_limit

# The library's own JSON error shapes, so a refusal drawn here is
# indistinguishable to the page from one drawn inside origami-auth. They are
# underscored but they are what every endpoint in that module returns. The
# body parser is common.helpers' instead: the library's lets invalid UTF-8
# and deep nesting raise (#767), and this one answers them with its 400.
from origami_common.helpers import _json_error, _permission_denied

from accounts.email_change import (
    ConfirmationOutcome,
    confirm_pending_address,
    link_is_valid,
    notify_previous_address,
    request_confirmation,
    user_from_uid,
)
from accounts.forms import (
    EmailChangeForm,
    NewAddressForm,
    NewPasswordForm,
    RegistrationForm,
    VerifiedAddressPasswordResetForm,
)
from accounts.mail_allowance import (
    ADDRESS_WINDOW_SECONDS,
    MAX_SENDS_PER_ADDRESS,
    address_allowance_spent,
    address_digest,
    charge_address,
)
from accounts.models import User
from accounts.password_reset import site_link_context
from accounts.text_limits import ACCOUNT_TEXT_LIMITS
from battle.access import actor_of, listable_battles
from battle.models import Battle, BattleCombatant
from characters.models import Character
from common.breadcrumbs import BreadcrumbMixin, step, trail
from common.decorators import internal_errors_logged
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
    onto the other's rooms. Logout points here rather than at a hard-coded
    ``/tarmar/`` for the same reason; sign-in and registration land on the
    theme's ``signed_in_landing_url_name`` instead (issue #952, see
    :func:`signed_in_landing_url`), so the front page stays the public front
    page for everyone.
    """

    permanent = False

    def get_redirect_url(self, *args, **kwargs):
        return reverse(request_theme(self.request).landing_url_name)


def signed_in_landing_url(request: HttpRequest) -> str:
    """Where a sign-in or a registration with no ``next`` of its own lands.

    The theme names the room (``Theme.signed_in_landing_url_name``): the
    player's journal on the default host (issue #952), the battle list on a
    classic-arena host, which keeps its own door (issue #269). The front door
    itself, :class:`FrontDoorView`, is not consulted: it still opens onto the
    public front page, the same for everyone.

    A visitor who has not answered the passphrase is sent to the battle list
    instead, which is public, rather than to a gated page that would stop
    them with no word that the sign-in worked (issue #694).

    Args:
        request: The request that has just signed the visitor in.

    Returns:
        The path to redirect to.
    """
    if not has_passed_gate(request):
        return reverse("battle:list")
    return reverse(request_theme(request).signed_in_landing_url_name)


class AccountTextLimitsMixin(ContextMixin):
    """Put ``account_text_limits`` in the page's context (issue #943).

    Every account page with a text input reads its ``maxlength`` and
    ``minlength`` from :data:`accounts.text_limits.ACCOUNT_TEXT_LIMITS`
    through this, so no template types a limit of its own.
    """

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Add the account text limits.

        Args:
            **kwargs: Passed through to the view's own context.

        Returns:
            The template context with ``account_text_limits`` set.
        """
        context = super().get_context_data(**kwargs)
        context["account_text_limits"] = ACCOUNT_TEXT_LIMITS
        return context


class RegisterView(
    AccountTextLimitsMixin, django_auth_views.RedirectURLMixin, auth_views.RegisterView
):
    template_name = "accounts/register.html"
    # The shared form, with password refusals moved off Confirm Password and
    # onto the password they are about (issue #711).
    form_class = RegistrationForm

    def get_success_url(self) -> str:
        """Land a new account where a sign-in lands, unless ``next`` says where.

        Registering signs the visitor in, so it follows the same rule as
        :class:`LoginView`: an explicit, same-site ``next`` wins (Django's
        ``RedirectURLMixin`` checks it), and otherwise
        :func:`signed_in_landing_url` decides -- the journal on the default
        host, the battle list on the arena's (issues #269, #952), and the
        battle list for a visitor who has not passed the gate (issue #694).

        Returns:
            The URL to redirect to after a successful registration.
        """
        return self.get_redirect_url() or signed_in_landing_url(self.request)

    def form_valid(self, form: RegistrationForm) -> HttpResponse:
        """Create and sign in the account, then mail its address a link.

        The address typed at registration is stored but not proved, so the
        reset form will not mail it until its link is opened (issue #931).
        Nothing here can stop the registration: an address over its hourly
        allowance, or a send that fails, leaves the account with an
        unconfirmed address, which its profile says, and from where the link
        can be asked for again.

        Args:
            form: The validated registration form.

        Returns:
            The library's redirect.
        """
        response = super().form_valid(form)
        account = cast(User, self.object)
        if account.email:
            request_confirmation(
                self.request, account, account.email, notify_previous=False
            )
        return response


class LoginView(AccountTextLimitsMixin, auth_views.LoginView):
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

        Until #952 a sign-in followed ``LOGIN_REDIRECT_URL`` to the front door,
        and on the studio host the front door is gated. So a visitor who came
        from the public arena, pressed the site menu's Login (which carries no
        ``?next=``) and typed the right password was answered with the
        passphrase form: no word that the sign-in had worked, no second secret
        explained, no way back (issue #694). ``common/public_paths`` names that
        exact trap in its own docstring and then walked into it one step later.

        An explicit ``?next=`` still wins -- the arena's own "Sign in" links
        carry one, and ``battle-sign-in-redirect.spec.js`` pins that. Without
        one, :func:`signed_in_landing_url` decides: the player's journal on
        the default host (issue #952), the battle list on the arena's, and the
        battle list, which is public, for a visitor who has not answered the
        passphrase, instead of a page that will stop them.

        Returns:
            The URL to redirect to after a successful sign-in.
        """
        return self.get_redirect_url() or signed_in_landing_url(self.request)

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


class PasswordResetView(AccountTextLimitsMixin, django_auth_views.PasswordResetView):
    """Ask for a reset link by email address (issue #929).

    Django's view, with this site's templates. Whatever address is typed, the
    visitor lands on the same "sent" page: Django mails only an active account
    with that address and a usable password, and says nothing either way, so
    the page cannot be used to learn whether an account exists. An account
    with no email address on file therefore gets no message; the host
    operator's route for it is ``manage.py password_reset_link``.

    Only an address that has been proved is mailed (issue #931,
    :class:`~accounts.forms.VerifiedAddressPasswordResetForm`): one nobody has
    confirmed may be a typo or a stranger's inbox.

    The page is outside the passphrase gate (``common.public_paths``), and it
    sends mail, so it carries two limits. Every POST is charged against the
    caller's network address, with the guessing budget the sign-in and gate
    limiters use; every POST counts, not only failures, since each one can
    send a message. That alone lets many sources fill one inbox, so each
    target address also gets ``RESET_PER_EMAIL_MAX_SENDS`` an hour
    (:meth:`form_valid`), an allowance it shares with the email-confirmation
    link (``accounts.mail_allowance``).

    When ``SITE_BASE_URL`` is set, the emailed link is built on it rather than
    on the request's Host header and scheme.
    """

    template_name = "accounts/password_reset_form.html"
    form_class = VerifiedAddressPasswordResetForm
    email_template_name = "accounts/password_reset_email.txt"
    subject_template_name = "accounts/password_reset_subject.txt"
    success_url = reverse_lazy("password_reset_done")

    RESET_MAX_REQUESTS = GUESS_MAX_ATTEMPTS
    RESET_WINDOW_SECONDS = GUESS_WINDOW_SECONDS
    RESET_PER_EMAIL_MAX_SENDS = MAX_SENDS_PER_ADDRESS
    RESET_PER_EMAIL_WINDOW_SECONDS = ADDRESS_WINDOW_SECONDS

    def post(
        self, request: HttpRequest, *args: object, **kwargs: object
    ) -> HttpResponse:
        """Charge the caller's address, then let Django handle the form.

        Args:
            request: The form's POST.
            *args: Passed through to Django's view.
            **kwargs: Passed through to Django's view.

        Returns:
            Django's response, or a 429 once this address has spent its
            allowance for the window.
        """
        cache_key = f"rate_limit:password_reset:ip_{client_ip(request)}"
        if current_count(cache_key) >= self.RESET_MAX_REQUESTS:
            return too_many_attempts(
                "Too many password reset requests from this network address. "
                "The count is per network address, not per account.",
                window_seconds=self.RESET_WINDOW_SECONDS,
                request=request,
                headline="Too many password reset requests.",
            )
        charge(cache_key, self.RESET_WINDOW_SECONDS)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form: PasswordResetForm) -> HttpResponse:
        """Send the link, unless this target address has had its share.

        The per-target counter is keyed on a hash of the lowercased address
        and charged whether or not any account has it, so its state says
        nothing about which addresses are registered. When it trips, the send
        is skipped and the visitor sees the same "sent" page as always: a 429
        here would tell the caller that this address is being asked about.

        Args:
            form: The validated reset form.

        Returns:
            The redirect to the "sent" page.
        """
        target = form.cleaned_data["email"]
        if address_allowance_spent(target):
            audit_logger.info(
                "PASSWORD_RESET_SUPPRESSED: target limit reached for "
                "email hash %s from %s",
                address_digest(target)[:12],
                client_ip(self.request),
            )
            return HttpResponseRedirect(self.get_success_url())
        charge_address(target)
        self.extra_email_context = site_link_context()
        return super().form_valid(form)


class PasswordResetDoneView(django_auth_views.PasswordResetDoneView):
    """The page after the request, the same whatever address was typed."""

    template_name = "accounts/password_reset_done.html"


class PasswordResetConfirmView(
    AccountTextLimitsMixin, django_auth_views.PasswordResetConfirmView
):
    """Choose the new password, from the link the email carried.

    Django's view: it checks the token, moves it out of the URL into the
    session, and shows the form, or the expired-link state when the token is
    wrong, used or out of date. Its form refuses a password past the limit
    registration holds to (:class:`accounts.forms.NewPasswordForm`).
    """

    template_name = "accounts/password_reset_confirm.html"
    form_class = NewPasswordForm
    success_url = reverse_lazy("password_reset_complete")


class PasswordResetCompleteView(django_auth_views.PasswordResetCompleteView):
    """The new password is set; the way on is the sign-in page."""

    template_name = "accounts/password_reset_complete.html"


class EmailChangeView(
    LoginRequiredMixin, AccountTextLimitsMixin, BreadcrumbMixin, FormView
):
    """Change your own email address: password first, then a link (#931).

    The address on file stays as it is. The new one is stored as pending and
    sent a confirmation link; the old one is sent a notice. The address
    changes only when the link is opened (:class:`EmailConfirmView`).

    This is the account holder's path, on their own account, an Admin's
    included. The one other path is an Admin setting another account's
    address from its profile (issue #954, :func:`profile_update_field`): that
    address lands unconfirmed, so password reset does not mail it until its
    own link is opened, and the old address is told. A DM has no such path.
    The host operator's routes are the shell and
    ``manage.py set_email_verified``.

    Every POST is charged against the caller's network address with the
    guessing budget, as on the reset form: it checks a password and it sends
    mail. The new address shares the reset form's hourly allowance per inbox.
    """

    template_name = "accounts/email_change.html"
    form_class = EmailChangeForm
    success_url = reverse_lazy("profile")
    login_url = "login"

    CHANGE_MAX_REQUESTS = GUESS_MAX_ATTEMPTS
    CHANGE_WINDOW_SECONDS = GUESS_WINDOW_SECONDS

    def get_breadcrumb(self, context: dict[str, Any]) -> list[dict[str, str]]:
        """Profile, then this page.

        Args:
            context: The template context (unused).

        Returns:
            The trail.
        """
        return trail(step("Profile", reverse("profile")), step("Change email"))

    def get_form_kwargs(self) -> dict[str, Any]:
        """Hand the form the signed-in account.

        Returns:
            The form's keyword arguments.
        """
        kwargs = super().get_form_kwargs()
        kwargs["user"] = cast(User, self.request.user)
        return kwargs

    def post(
        self, request: HttpRequest, *args: object, **kwargs: object
    ) -> HttpResponse:
        """Charge the caller's network address, then handle the form.

        Args:
            request: The form's POST.
            *args: Passed through.
            **kwargs: Passed through.

        Returns:
            The form's response, or a 429 once the address has spent its
            allowance for the window.
        """
        cache_key = f"rate_limit:email_change:ip_{client_ip(request)}"
        if current_count(cache_key) >= self.CHANGE_MAX_REQUESTS:
            return too_many_attempts(
                "Too many email change requests from this network address. "
                "The count is per network address, not per account.",
                window_seconds=self.CHANGE_WINDOW_SECONDS,
                request=request,
                headline="Too many email change requests.",
            )
        charge(cache_key, self.CHANGE_WINDOW_SECONDS)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form: BaseForm) -> HttpResponse:
        """Show the form again, auditing a wrong password.

        Args:
            form: The form with its errors.

        Returns:
            The page with the errors beside their fields.
        """
        if "current_password" in form.errors:
            account = cast(User, self.request.user)
            audit_logger.info(
                "EMAIL_CHANGE_REFUSED: wrong password for user=%s (pk=%s) from %s",
                account.get_username(),
                account.pk,
                client_ip(self.request),
            )
        return super().form_invalid(form)

    def form_valid(self, form: BaseForm) -> HttpResponse:
        """Store the new address as pending and send both messages.

        Args:
            form: The validated form.

        Returns:
            The redirect to the profile, which shows the pending address, or
            the form again when the link could not be sent.
        """
        account = cast(User, self.request.user)
        outcome = request_confirmation(
            self.request,
            account,
            form.cleaned_data["new_email"],
            notify_previous=True,
        )
        if outcome is ConfirmationOutcome.ADDRESS_LIMITED:
            form.add_error(
                "new_email",
                "That address has been sent as many messages from this site "
                "as it may be in an hour. Nothing has changed; try again later.",
            )
            return super().form_invalid(form)
        if outcome is ConfirmationOutcome.SEND_FAILED:
            form.add_error(
                None,
                "The confirmation message could not be sent, so nothing has "
                "changed. Try again later, or ask the site's operator.",
            )
            return super().form_invalid(form)
        return super().form_valid(form)


class EmailConfirmView(TemplateView):
    """The page a confirmation link opens: one button makes the change (#931).

    Opening the link only shows what it will do; the change is a POST. Mail
    scanners and link previews fetch a URL, and a single-use link spent by a
    scanner is a link its owner can no longer use.

    The page is outside the passphrase gate, under the reset prefix
    (``common.public_paths``), for the reason the reset link is: it is opened
    in whatever browser the mail client picks, and the gate answers a correct
    passphrase by redirecting to ``/``, which would lose the token. Nobody has
    to be signed in: the link is the proof that its reader has the inbox, and
    the password was asked for when the change was.
    """

    template_name = "accounts/email_confirm.html"

    def get(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Show the address the link confirms, or say the link is spent.

        Args:
            request: The GET.
            *args: Unused.
            **kwargs: ``uidb64`` and ``token`` from the URL.

        Returns:
            The page.
        """
        account = user_from_uid(kwargs["uidb64"])
        valid = link_is_valid(account, kwargs["token"])
        context = self.get_context_data(validlink=valid, confirmed=False)
        if valid and account is not None:
            context["account_username"] = account.get_username()
            context["pending_email"] = account.pending_email
        return self.render_to_response(context)

    def post(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        """Make the change, if the link is still good.

        The account row is locked while it is checked and written, so two
        presses of the button cannot both act on one link.

        Args:
            request: The POST from the page's button.
            *args: Unused.
            **kwargs: ``uidb64`` and ``token`` from the URL.

        Returns:
            The page, saying the address is confirmed or the link is spent.
        """
        with transaction.atomic():
            account = user_from_uid(kwargs["uidb64"])
            if account is not None:
                account = User.objects.select_for_update().get(pk=account.pk)
            if account is None or not link_is_valid(account, kwargs["token"]):
                return self.render_to_response(
                    self.get_context_data(validlink=False, confirmed=False)
                )
            confirmed_address = confirm_pending_address(account)
        return self.render_to_response(
            self.get_context_data(
                validlink=True,
                confirmed=True,
                account_username=account.get_username(),
                confirmed_email=confirmed_address,
            )
        )


class ProfileView(AccountTextLimitsMixin, BreadcrumbMixin, auth_views.ProfileView):
    template_name = "accounts/profile.html"
    login_url = "login"
    breadcrumb_label = "Profile"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """The library's context, plus whether the address can be set here.

        Args:
            **kwargs: Passed to the library's view.

        Returns:
            The context, with ``admin_sets_email`` True when an Admin is
            looking at another account (issue #954, :func:`_admin_sets_email`).
        """
        context = super().get_context_data(**kwargs)
        viewer = cast(User, self.request.user)
        context["admin_sets_email"] = (
            viewer.is_role_admin and context["profile_user"].pk != viewer.pk
        )
        return context


class UsersListView(BreadcrumbMixin, auth_views.UsersListView):
    template_name = "accounts/users_list.html"
    login_url = "login"
    breadcrumb_label = "Users"


class LandingView(TemplateView):
    template_name = "landing.html"


#: How much of each list the journal page shows (issues #827, #924, #952); the
#: characters list and the Games Master's and battle pages hold the rest.
JOURNAL_CHARACTER_LIMIT = 6
JOURNAL_SESSION_LIMIT = 3
JOURNAL_BATTLE_LIMIT = 3


@dataclass(frozen=True)
class PlayerJournal:
    """What belongs to the signed-in player, as the journal page lists it.

    Attributes:
        characters: Their own undeleted characters, living or dead, newest
            first, at most ``JOURNAL_CHARACTER_LIMIT``.
        character_count: How many such characters they have in all.
        sessions: Their latest Games Master sessions.
        battles: The undeleted battles they made or hold a seat in, most
            recently changed first.
    """

    characters: list[Character]
    character_count: int
    sessions: list[GMSession]
    battles: list[Battle]


def player_journal(request: HttpRequest) -> PlayerJournal:
    """The signed-in player's characters, sessions and battles (issue #827).

    Every list is read from the database on each visit, so the page shows
    what is true now rather than what happened on the way in.

    Args:
        request: A request from a signed-in user.

    Returns:
        The player's journal.
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
    return PlayerJournal(
        characters=list(own_characters[:JOURNAL_CHARACTER_LIMIT]),
        character_count=own_characters.count(),
        sessions=list(GMSession.objects.filter(user=user)[:JOURNAL_SESSION_LIMIT]),
        battles=list(battles[:JOURNAL_BATTLE_LIMIT]),
    )


class HomeView(ActivePageMixin, TemplateView):
    """The public front page, the same signed in or out (issue #924).

    It carried the player's own characters, sessions and battles on top from
    #827 until #924 gave them their own page, :class:`JournalView`, so the
    front page is the front page for everyone. A sign-in lands on the journal
    instead (issue #952), never here.
    """

    template_name = "accounts/home.html"
    active_page = "home"


class JournalView(LoginRequiredMixin, BreadcrumbMixin, ActivePageMixin, TemplateView):
    """The signed-in player's journal: characters, sessions, battles (#924, #952).

    The site menu links here beside Characters and Parties once signed in, and
    a sign-in on the default host lands here (:func:`signed_in_landing_url`). A
    signed-out visitor is sent to sign in and brought back.
    """

    template_name = "accounts/journal.html"
    active_page = "journal"
    breadcrumb_label = "Your journal"

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Add the player's journal, read fresh from the database.

        Args:
            **kwargs: The URL's keyword arguments, passed through.

        Returns:
            The template context with ``journal`` set.
        """
        context = super().get_context_data(**kwargs)
        context["journal"] = player_journal(self.request)
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

#: The profile field the library would write directly, and why it may not.
EMAIL_FIELD = "email"
EMAIL_FIELD_REFUSAL = (
    "The email address changes on its own page, with your password and a link "
    "sent to the new address."
)
#: What the Admin's set-address path answers when the link could not go.
EMAIL_SET_LIMITED = (
    "That address has been sent as many messages from this site as it may be "
    "in an hour. Nothing has changed; try again later."
)
EMAIL_SET_SEND_FAILED = (
    "The confirmation message could not be sent, so nothing has changed. Try "
    "again later."
)

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
@internal_errors_logged(json_only=True)
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


# The library's own limit on this endpoint, in its own bucket: the address is
# set here rather than in the library, so the library's decorator never runs
# for it, and an Admin's address writes count with their other profile writes.
@rate_limit("profile_update", max_requests=30, window_seconds=60)
def _admin_sets_email(
    request: HttpRequest, target_user_id: int, value: object
) -> HttpResponse:
    """An Admin sets another account's address, unconfirmed (issue #954).

    Spencer's ruling on #931: an Admin, and only an Admin, may set another
    account's address from the manager path. The address lands on the account
    with ``email_verified`` False, so password reset does not mail it; the
    confirmation link goes to it through
    :func:`~accounts.email_change.request_confirmation`, which charges the
    per-address allowance; the old confirmed address is sent the notice under
    #931's allowance rule; and the audit line names both accounts. Nothing
    else on the account changes.

    The caller has already been checked: signed in, holding the Admin role,
    and naming an account other than their own.

    Args:
        request: The AJAX POST from another account's profile page.
        target_user_id: The account whose address is set.
        value: The posted address, unchecked.

    Returns:
        ``{"success": true}``, a 400 naming why the address was refused or
        not sent, or the library's 403 for an account that does not exist.
    """
    actor = cast(User, request.user)
    with transaction.atomic():
        target_user = User.objects.select_for_update().filter(pk=target_user_id).first()
        if target_user is None:
            return _permission_denied()
        form = NewAddressForm(
            target_user, data={"new_email": value if isinstance(value, str) else ""}
        )
        if not form.is_valid():
            return _json_error(form.errors["new_email"][0])
        address = form.cleaned_data["new_email"]
        previous_address = target_user.email
        previous_verified = target_user.email_verified
        # The link is sent inside the transaction: the save depends on it.
        outcome = request_confirmation(
            request, target_user, address, notify_previous=False, set_by=actor
        )
    if outcome is ConfirmationOutcome.ADDRESS_LIMITED:
        return _json_error(EMAIL_SET_LIMITED)
    if outcome is ConfirmationOutcome.SEND_FAILED:
        return _json_error(EMAIL_SET_SEND_FAILED)
    # The notice is not, so the row is not held locked through a slow mail
    # server for a message nothing waits on.
    notify_previous_address(
        request,
        target_user,
        previous_address,
        previous_verified=previous_verified,
        address=address,
        set_by=actor,
    )
    return JsonResponse({"success": True})


@login_required
@require_POST
@internal_errors_logged(json_only=True)
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

    The email address is refused (issue #931): it changes on
    :class:`EmailChangeView`, with the password and a confirmation link. The
    one exception is an Admin setting another account's address on the
    manager path (issue #954, :func:`_admin_sets_email`); an Admin's own
    address, on either path, goes through the change form like anyone's.

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
    if field == EMAIL_FIELD:
        # The library writes the address on a plain POST, which would make a
        # signed-in browser enough to take the account through password reset
        # (issue #931). It changes on its own page, with the password and a
        # link to the new address, except when an Admin sets another
        # account's, which lands unconfirmed (#954). Everyone else gets the
        # same answer for every target, before any lookup, so it says nothing
        # about who exists.
        if (
            user_id is not None
            and request.user.is_role_admin
            and user_id != request.user.pk
        ):
            return _admin_sets_email(request, user_id, data.get("value"))
        return _json_error(EMAIL_FIELD_REFUSAL)
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
