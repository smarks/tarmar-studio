"""Changing an account's email address takes the password and a link (#931).

Since password reset (#929) the address on an account is a recovery
credential. These tests walk each step: the password asked for, the new
address held as pending while a link goes to it and a notice to the old one,
the link that switches the address once, the links that must not (expired,
used, replaced, tampered), the reset that mails only a proved address, the
limits, registration, the operator command and the migration.

Mail goes to Django's locmem backend, so ``mail.outbox`` is what was sent.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from io import StringIO
from typing import TYPE_CHECKING
from unittest.mock import patch

from django.conf import settings
from django.contrib.admin.sites import site as admin_site
from django.contrib.auth.models import AnonymousUser
from django.core import mail
from django.core.management import CommandError, call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.forms import modelform_factory
from django.test import (
    Client,
    RequestFactory,
    TestCase,
    TransactionTestCase,
    override_settings,
)

from accounts.email_change import EmailChangeTokenGenerator
from accounts.mail_allowance import MAX_SENDS_PER_ADDRESS
from accounts.models import User
from accounts.views import (
    EMAIL_FIELD_REFUSAL,
    EMAIL_SET_LIMITED,
    EMAIL_SET_SEND_FAILED,
    EmailChangeView,
)
from common.public_paths import is_public_path
from common.test_helpers import TEST_PASSWORD, BaseTestCase

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse as TestResponse

CHANGE_URL = "/tarmar/accounts/profile/email/"
PROFILE_URL = "/tarmar/accounts/profile/"
RESET_URL = "/tarmar/accounts/password_reset/"
OLD_EMAIL = "old@example.test"
NEW_EMAIL = "new@example.test"
CONFIRM_LINK = re.compile(
    r"(?P<scheme>https?)://(?P<host>[^/\s]+)"
    r"(?P<path>/tarmar/accounts/password_reset/address/[^/\s]+/[^/\s]+/)"
)


def message_to(address: str) -> mail.EmailMessage:
    """The one message sent to ``address`` so far."""
    sent = [message for message in mail.outbox if message.to == [address]]
    assert len(sent) == 1, [message.to for message in mail.outbox]
    return sent[0]


def link_in(message: mail.EmailMessage) -> str:
    """The confirmation path a message carries."""
    match = CONFIRM_LINK.search(str(message.body))
    assert match is not None, message.body
    return match.group("path")


class EmailChangeTestCase(BaseTestCase):
    """One signed-in account whose address was proved before."""

    def setUp(self) -> None:
        super().setUp()
        self.user = self.create_user(
            username="spencer", email=OLD_EMAIL, email_verified=True
        )
        self.login_as(self.user)

    def request_change(
        self,
        address: str = NEW_EMAIL,
        password: str = TEST_PASSWORD,
        network_address: str = "127.0.0.1",
    ) -> TestResponse:
        """POST the change form from ``network_address``."""
        return self.client.post(
            CHANGE_URL,
            {"new_email": address, "current_password": password},
            REMOTE_ADDR=network_address,
        )

    def requested_link(self, address: str = NEW_EMAIL) -> str:
        """Ask for ``address`` and return the link sent to it."""
        mail.outbox.clear()
        self.request_change(address)
        return link_in(message_to(address))


class ChangePageTest(EmailChangeTestCase):
    def test_signed_out_visitors_are_sent_to_sign_in(self) -> None:
        self.logout()
        response = self.client.get(CHANGE_URL)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/tarmar/accounts/login/", response["Location"])

    def test_the_page_asks_for_the_address_and_the_password(self) -> None:
        response = self.client.get(CHANGE_URL)
        self.assertContains(response, "<h1>Change email address</h1>", html=True)
        self.assertContains(response, 'name="new_email"')
        self.assertContains(response, 'name="current_password"')

    def test_the_profile_links_here_and_no_longer_autosaves_the_address(
        self,
    ) -> None:
        response = self.client.get(PROFILE_URL)
        self.assertContains(response, f'href="{CHANGE_URL}" id="email-change-link"')
        self.assertContains(response, 'id="email-status">Confirmed.')
        self.assertNotContains(response, 'data-field="email"')

    def test_another_accounts_profile_offers_a_dm_no_change(self) -> None:
        # A DM manages accounts but may not set an address (#954); an Admin
        # may, and AdminSetsAddressTest covers that page.
        manager = self.create_dm(username="manager")
        self.logout()
        self.login_as(manager)
        response = self.client.get(f"/tarmar/accounts/profile/{self.user.pk}/")
        self.assertContains(response, OLD_EMAIL)
        self.assertNotContains(response, 'id="email-change-link"')
        self.assertContains(response, "Only the account holder can change")


class WrongPasswordTest(EmailChangeTestCase):
    def test_a_wrong_password_changes_and_sends_nothing(self) -> None:
        with self.assertLogs("origami.audit", level="INFO") as logs:
            response = self.request_change(password="not-the-password")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "That is not the password for this account.")
        self.assertEqual(mail.outbox, [])
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, OLD_EMAIL)
        self.assertEqual(self.user.pending_email, "")
        self.assertIn("EMAIL_CHANGE_REFUSED", logs.output[0])


class RequestTest(EmailChangeTestCase):
    def test_the_new_address_is_pending_and_two_messages_go(self) -> None:
        response = self.request_change()
        self.assertRedirects(response, PROFILE_URL)

        self.user.refresh_from_db()
        self.assertEqual(self.user.email, OLD_EMAIL)
        self.assertTrue(self.user.email_verified)
        self.assertEqual(self.user.pending_email, NEW_EMAIL)
        self.assertIsNotNone(self.user.pending_email_sent_at)

        self.assertEqual(len(mail.outbox), 2)
        confirmation = message_to(NEW_EMAIL)
        self.assertEqual(confirmation.from_email, settings.DEFAULT_FROM_EMAIL)
        self.assertEqual(
            confirmation.subject, "Confirm your email address at testserver"
        )
        self.assertIn('the account "spencer"', str(confirmation.body))
        link_in(confirmation)

        notice = message_to(OLD_EMAIL)
        self.assertEqual(
            notice.subject, "Your email address at testserver is being changed"
        )
        self.assertIn("If this was not you", str(notice.body))
        self.assertIsNone(CONFIRM_LINK.search(str(notice.body)))

    def test_the_profile_shows_the_pending_address(self) -> None:
        self.request_change()
        response = self.client.get(PROFILE_URL)
        self.assertContains(response, 'id="email-pending"')
        self.assertContains(response, NEW_EMAIL)
        self.assertContains(response, f'value="{OLD_EMAIL}"')

    def test_the_profile_says_when_the_link_was_sent_in_the_readers_zone(self) -> None:
        """The time is a <time> element the page writes locally (issue #62)."""
        self.request_change()
        self.user.refresh_from_db()
        assert self.user.pending_email_sent_at is not None

        response = self.client.get(PROFILE_URL)

        self.assertContains(
            response,
            f'<time class="local-time" '
            f'datetime="{self.user.pending_email_sent_at.isoformat()}" '
            'data-local-time-style="datetime">',
        )

    def test_the_confirmed_address_itself_is_refused(self) -> None:
        response = self.request_change(OLD_EMAIL.upper())
        self.assertContains(response, "already the confirmed address")
        self.assertEqual(mail.outbox, [])

    def test_an_unconfirmed_address_can_be_sent_its_link_again(self) -> None:
        self.user.email_verified = False
        self.user.save()
        self.request_change(OLD_EMAIL)
        # The link goes to the address; there is no other address to notify.
        self.assertEqual([message.to for message in mail.outbox], [[OLD_EMAIL]])

    def test_a_failed_send_changes_nothing(self) -> None:
        with (
            patch(
                "accounts.email_change.send_mail",
                side_effect=ConnectionRefusedError("no mail server"),
            ),
            self.assertLogs("accounts.email_change", level="ERROR"),
        ):
            response = self.request_change()
        self.assertContains(response, "could not be sent")
        self.user.refresh_from_db()
        self.assertEqual(self.user.pending_email, "")

    def test_a_failed_notice_does_not_block_the_change(self) -> None:
        """A dead old address is the usual reason to change it."""
        real_send = mail.send_mail

        def refuse_the_old_address(*args: object, **kwargs: object) -> int:
            if args[3] == [OLD_EMAIL]:
                raise ConnectionRefusedError("old mailbox gone")
            return real_send(*args, **kwargs)  # type: ignore[arg-type]

        with (
            patch(
                "accounts.email_change.send_mail", side_effect=refuse_the_old_address
            ),
            self.assertLogs("accounts.email_change", level="ERROR") as logs,
        ):
            response = self.request_change()
        self.assertRedirects(response, PROFILE_URL)
        self.assertIn("email-change notice", logs.output[0])
        self.user.refresh_from_db()
        self.assertEqual(self.user.pending_email, NEW_EMAIL)
        link_in(message_to(NEW_EMAIL))

    def test_the_token_is_only_made_for_this_sites_accounts(self) -> None:
        with self.assertRaises(TypeError):
            EmailChangeTokenGenerator().make_token(AnonymousUser())  # type: ignore[arg-type]

    def test_the_request_is_audited(self) -> None:
        with self.assertLogs("origami.audit", level="INFO") as logs:
            self.request_change()
        self.assertIn("EMAIL_CHANGE_REQUESTED", "\n".join(logs.output))

    @override_settings(SITE_BASE_URL="https://tarmar.example")
    def test_the_link_is_built_on_site_base_url(self) -> None:
        self.request_change()
        match = CONFIRM_LINK.search(str(message_to(NEW_EMAIL).body))
        assert match is not None
        self.assertEqual(match.group("scheme"), "https")
        self.assertEqual(match.group("host"), "tarmar.example")


class NoticeAllowanceTest(EmailChangeTestCase):
    """The notice to the old address cannot be used to fill an inbox."""

    def test_an_unconfirmed_registered_address_gets_no_notices(self) -> None:
        victim = "victim@example.test"
        self.logout()
        self.client.post(
            "/tarmar/accounts/register/",
            {
                "username": "spammer",
                "password1": "LanternBridge42x",
                "password2": "LanternBridge42x",
                "email": victim,
                "preferred_contact": "email",
            },
        )
        for attempt in range(MAX_SENDS_PER_ADDRESS + 2):
            self.request_change(f"elsewhere{attempt}@example.test", "LanternBridge42x")
        to_victim = [message for message in mail.outbox if message.to == [victim]]
        self.assertLessEqual(len(to_victim), MAX_SENDS_PER_ADDRESS)
        # Only the registration's own confirmation link: no notices at all.
        self.assertEqual(len(to_victim), 1)
        # The change requests themselves went through.
        self.assertEqual(
            User.objects.get(username="spammer").pending_email,
            f"elsewhere{MAX_SENDS_PER_ADDRESS + 1}@example.test",
        )

    def test_a_confirmed_address_gets_at_most_its_share_of_notices(self) -> None:
        for attempt in range(MAX_SENDS_PER_ADDRESS + 2):
            response = self.request_change(f"elsewhere{attempt}@example.test")
            self.assertRedirects(response, PROFILE_URL)
        to_old = [message for message in mail.outbox if message.to == [OLD_EMAIL]]
        self.assertEqual(len(to_old), MAX_SENDS_PER_ADDRESS)
        self.user.refresh_from_db()
        self.assertEqual(
            self.user.pending_email,
            f"elsewhere{MAX_SENDS_PER_ADDRESS + 1}@example.test",
        )


class AdminSaveUnconfirmsTest(TestCase):
    """An address typed into Django's admin is not proved by being typed."""

    def setUp(self) -> None:
        self.user = User.objects.create_user(
            username="spencer",
            password=TEST_PASSWORD,
            email=OLD_EMAIL,
            email_verified=True,
        )
        self.model_admin = admin_site._registry[User]
        self.request = RequestFactory().post("/admin/")

    def save_through_admin(self, data: dict[str, object]) -> User:
        form_class = modelform_factory(User, fields=["email", "email_verified"])
        form = form_class(data=data, instance=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        self.model_admin.save_model(self.request, form.instance, form, change=True)
        self.user.refresh_from_db()
        return self.user

    def test_a_changed_address_is_unconfirmed(self) -> None:
        saved = self.save_through_admin({"email": NEW_EMAIL, "email_verified": "on"})
        self.assertEqual(saved.email, NEW_EMAIL)
        self.assertFalse(saved.email_verified)

    def test_ticking_the_flag_in_the_same_save_stands(self) -> None:
        self.user.email_verified = False
        self.user.save()
        saved = self.save_through_admin({"email": NEW_EMAIL, "email_verified": "on"})
        self.assertTrue(saved.email_verified)

    def test_an_unchanged_address_keeps_its_flag(self) -> None:
        saved = self.save_through_admin({"email": OLD_EMAIL, "email_verified": "on"})
        self.assertTrue(saved.email_verified)


class AutosaveRefusesTheAddressTest(EmailChangeTestCase):
    def test_a_dm_cannot_set_another_accounts_address(self) -> None:
        # Only an Admin may (#954); AdminSetsAddressTest covers that path.
        manager = self.create_dm(username="manager")
        self.logout()
        self.login_as(manager)
        response = self.post_json(
            f"/tarmar/accounts/profile/{self.user.pk}/update-field/",
            {"field": "email", "value": "manager@example.test"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], EMAIL_FIELD_REFUSAL)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, OLD_EMAIL)
        self.assertTrue(self.user.email_verified)
        self.assertEqual(self.user.pending_email, "")
        self.assertEqual(mail.outbox, [])


class AdminSetsAddressTest(EmailChangeTestCase):
    """An Admin sets another account's address, unconfirmed (issue #954)."""

    def setUp(self) -> None:
        super().setUp()
        self.user.real_name = "Spencer Marks"
        self.user.phone = "+15555550100"
        self.user.discord = "spencer"
        self.user.preferred_contact = "email"
        self.user.is_player = True
        self.user.save()
        self.admin = self.create_admin(username="manager", email="admin@example.test")
        self.logout()
        self.login_as(self.admin)

    def set_address(
        self, address: object = NEW_EMAIL, user_id: int | None = None
    ) -> TestResponse:
        """POST the address on the manager path for ``user_id``."""
        target = self.user.pk if user_id is None else user_id
        return self.post_json(
            f"/tarmar/accounts/profile/{target}/update-field/",
            {"field": "email", "value": address},
        )

    def assert_unchanged(self) -> None:
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, OLD_EMAIL)
        self.assertTrue(self.user.email_verified)
        self.assertEqual(self.user.pending_email, "")

    def test_the_address_lands_unconfirmed_and_both_messages_go(self) -> None:
        before = User.objects.get(pk=self.user.pk)
        with self.assertLogs("origami.audit", level="INFO") as logs:
            response = self.set_address()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"success": True})

        self.user.refresh_from_db()
        self.assertEqual(self.user.email, NEW_EMAIL)
        self.assertFalse(self.user.email_verified)
        self.assertEqual(self.user.pending_email, NEW_EMAIL)
        self.assertIsNotNone(self.user.pending_email_sent_at)
        changed = {"email", "email_verified", "pending_email", "pending_email_sent_at"}
        for field in User._meta.concrete_fields:
            if field.attname not in changed:
                with self.subTest(field=field.attname):
                    self.assertEqual(
                        getattr(self.user, field.attname),
                        getattr(before, field.attname),
                    )

        self.assertEqual(len(mail.outbox), 2)
        confirmation = message_to(NEW_EMAIL)
        self.assertIn("An Admin at testserver made", str(confirmation.body))
        link_in(confirmation)
        notice = message_to(OLD_EMAIL)
        self.assertIn('The Admin "manager" at testserver changed', str(notice.body))
        self.assertIsNone(CONFIRM_LINK.search(str(notice.body)))

        audit = "\n".join(logs.output)
        self.assertIn("EMAIL_SET_BY_ADMIN", audit)
        self.assertIn(f"admin=manager (pk={self.admin.pk})", audit)
        self.assertIn(f"user=spencer (pk={self.user.pk})", audit)
        self.assertIn(f"from {OLD_EMAIL} to {NEW_EMAIL}", audit)
        self.assertNotIn("EMAIL_CHANGE_REQUESTED", audit)

    def test_the_link_confirms_it_and_reset_waits_for_it(self) -> None:
        self.set_address()
        path = link_in(message_to(NEW_EMAIL))
        mail.outbox.clear()
        Client().post(RESET_URL, {"email": NEW_EMAIL})
        self.assertEqual(mail.outbox, [])

        Client().post(path)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, NEW_EMAIL)
        self.assertTrue(self.user.email_verified)
        self.assertEqual(self.user.pending_email, "")

    def test_an_admin_is_refused_on_their_own_account(self) -> None:
        for url in (
            f"/tarmar/accounts/profile/{self.admin.pk}/update-field/",
            "/tarmar/accounts/profile/update-field/",
        ):
            with self.subTest(url=url):
                response = self.post_json(url, {"field": "email", "value": NEW_EMAIL})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"], EMAIL_FIELD_REFUSAL)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.email, "admin@example.test")
        self.assertEqual(self.admin.pending_email, "")
        self.assertEqual(mail.outbox, [])

    def test_a_dm_and_a_player_are_refused(self) -> None:
        for account in (
            self.create_dm(username="dungeon"),
            self.create_player(username="player"),
        ):
            with self.subTest(account=account.username):
                self.logout()
                self.login_as(account)
                response = self.set_address()
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"], EMAIL_FIELD_REFUSAL)
        self.assert_unchanged()
        self.assertEqual(mail.outbox, [])

    def test_an_unknown_account_is_the_librarys_403(self) -> None:
        response = self.set_address(user_id=self.user.pk + 1000)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(mail.outbox, [])

    def test_a_malformed_or_confirmed_address_is_refused(self) -> None:
        for value, error in (
            ("not-an-address", "Enter a valid email address."),
            ("", "This field is required."),
            (["new@example.test"], "This field is required."),
            (OLD_EMAIL.upper(), "already the confirmed address"),
        ):
            with self.subTest(value=value):
                response = self.set_address(value)
                self.assertEqual(response.status_code, 400)
                self.assertIn(error, response.json()["error"])
        self.assert_unchanged()
        self.assertEqual(mail.outbox, [])

    def test_the_allowance_is_charged(self) -> None:
        for attempt in range(MAX_SENDS_PER_ADDRESS):
            response = self.set_address()
            self.assertEqual(response.status_code, 200, msg=f"attempt {attempt}")
        sent_at = User.objects.get(pk=self.user.pk).pending_email_sent_at
        with self.assertLogs("origami.audit", level="INFO") as logs:
            response = self.set_address()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], EMAIL_SET_LIMITED)
        self.assertIn("EMAIL_CONFIRMATION_SUPPRESSED", logs.output[0])
        self.assertIn(
            f"user=spencer (pk={self.user.pk}) set by admin=manager "
            f"(pk={self.admin.pk})",
            logs.output[0],
        )
        to_new = [message for message in mail.outbox if message.to == [NEW_EMAIL]]
        self.assertEqual(len(to_new), MAX_SENDS_PER_ADDRESS)
        self.assertEqual(
            User.objects.get(pk=self.user.pk).pending_email_sent_at, sent_at
        )

    def test_the_allowance_is_the_one_the_reset_form_spends(self) -> None:
        for source in range(MAX_SENDS_PER_ADDRESS):
            Client().post(
                RESET_URL, {"email": NEW_EMAIL}, REMOTE_ADDR=f"192.0.2.{source + 1}"
            )
        response = self.set_address()
        self.assertEqual(response.json()["error"], EMAIL_SET_LIMITED)
        self.assert_unchanged()
        self.assertEqual(mail.outbox, [])

    def test_the_old_address_gets_its_notice_only_within_its_allowance(
        self,
    ) -> None:
        for source in range(MAX_SENDS_PER_ADDRESS):
            Client().post(
                RESET_URL, {"email": OLD_EMAIL}, REMOTE_ADDR=f"192.0.2.{source + 1}"
            )
        mail.outbox.clear()
        response = self.set_address()
        self.assertEqual(response.status_code, 200)
        self.assertEqual([message.to for message in mail.outbox], [[NEW_EMAIL]])

    def test_no_notice_when_the_old_address_was_not_confirmed(self) -> None:
        self.user.email_verified = False
        self.user.save()
        self.set_address()
        self.assertEqual([message.to for message in mail.outbox], [[NEW_EMAIL]])

    def test_a_failed_send_changes_nothing(self) -> None:
        with (
            patch(
                "accounts.email_change.send_mail",
                side_effect=ConnectionRefusedError("no mail server"),
            ),
            self.assertLogs("accounts.email_change", level="ERROR") as logs,
        ):
            response = self.set_address()
        self.assertEqual(response.json()["error"], EMAIL_SET_SEND_FAILED)
        self.assertIn("confirmation link", logs.output[0])
        self.assertIn(f"set by admin=manager (pk={self.admin.pk})", logs.output[0])
        self.assert_unchanged()

    def test_a_failed_notice_does_not_undo_the_saved_address(self) -> None:
        real_send = mail.send_mail

        def refuse_the_old_address(*args: object, **kwargs: object) -> int:
            if args[3] == [OLD_EMAIL]:
                raise ConnectionRefusedError("old mailbox gone")
            return real_send(*args, **kwargs)  # type: ignore[arg-type]

        with (
            patch(
                "accounts.email_change.send_mail", side_effect=refuse_the_old_address
            ),
            self.assertLogs("accounts.email_change", level="ERROR") as logs,
        ):
            response = self.set_address()
        self.assertEqual(response.json(), {"success": True})
        self.assertIn("email-change notice", logs.output[0])
        self.assertIn(f"set by admin=manager (pk={self.admin.pk})", logs.output[0])
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, NEW_EMAIL)
        self.assertFalse(self.user.email_verified)
        self.assertEqual(self.user.pending_email, NEW_EMAIL)
        link_in(message_to(NEW_EMAIL))

    def test_it_replaces_a_change_the_holder_had_started(self) -> None:
        self.logout()
        self.login_as(self.user)
        holder_link = self.requested_link("holder@example.test")
        self.logout()
        self.login_as(self.admin)
        mail.outbox.clear()

        self.set_address()
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, NEW_EMAIL)
        self.assertFalse(self.user.email_verified)
        self.assertEqual(self.user.pending_email, NEW_EMAIL)
        # The holder's link no longer confirms anything.
        response = Client().post(holder_link)
        self.assertContains(response, "Confirmation link not valid")
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, NEW_EMAIL)
        self.assertFalse(self.user.email_verified)
        # The admin's link still does.
        Client().post(link_in(message_to(NEW_EMAIL)))
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)

    def test_an_account_with_no_address_is_audited_as_none(self) -> None:
        self.user.email = ""
        self.user.email_verified = False
        self.user.save()
        with self.assertLogs("origami.audit", level="INFO") as logs:
            self.set_address()
        self.assertIn(f"from (none) to {NEW_EMAIL}", "\n".join(logs.output))
        self.assertEqual([message.to for message in mail.outbox], [[NEW_EMAIL]])

    def test_the_profile_offers_an_admin_the_set_button(self) -> None:
        response = self.client.get(f"/tarmar/accounts/profile/{self.user.pk}/")
        self.assertContains(response, 'id="profile-email" data-field="email"')
        self.assertContains(response, 'id="email-set-button"')
        self.assertNotContains(response, "Only the account holder can change")
        own = self.client.get(PROFILE_URL)
        self.assertNotContains(own, 'id="email-set-button"')
        self.assertContains(own, 'id="email-change-link"')

    def test_the_profile_shows_the_unconfirmed_state(self) -> None:
        self.set_address()
        response = self.client.get(f"/tarmar/accounts/profile/{self.user.pk}/")
        self.assertContains(response, f'value="{NEW_EMAIL}"')
        self.assertContains(response, 'id="email-status">Not confirmed yet')
        self.assertContains(response, "It is confirmed when that link is opened.")

    def test_the_users_page_shows_the_unconfirmed_state(self) -> None:
        page = self.client.get("/tarmar/accounts/users/")
        self.assertContains(page, f'<td data-label="Email">{OLD_EMAIL}</td>', html=True)
        self.set_address()
        page = self.client.get("/tarmar/accounts/users/")
        self.assertContains(
            page,
            f'<td data-label="Email">{NEW_EMAIL} '
            '<span class="email-unconfirmed">(not confirmed)</span></td>',
            html=True,
        )


class ConfirmTest(EmailChangeTestCase):
    def test_the_link_is_outside_the_gate(self) -> None:
        path = self.requested_link()
        self.assertTrue(is_public_path(path))
        response = Client().get(path)
        self.assertTemplateUsed(response, "accounts/email_confirm.html")
        self.assertNotContains(response, 'name="passphrase"')

    def test_opening_the_link_shows_the_change_and_makes_none(self) -> None:
        path = self.requested_link()
        response = Client().get(path)
        self.assertContains(response, "<h1>Confirm your email address</h1>", html=True)
        self.assertContains(response, NEW_EMAIL)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, OLD_EMAIL)

    def test_the_button_switches_the_address_once(self) -> None:
        path = self.requested_link()
        mail_client = Client()
        with self.assertLogs("origami.audit", level="INFO") as logs:
            response = mail_client.post(path)
        self.assertContains(response, 'id="email-confirmed"')
        self.assertIn("EMAIL_CHANGE_CONFIRMED", "\n".join(logs.output))
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, NEW_EMAIL)
        self.assertTrue(self.user.email_verified)
        self.assertEqual(self.user.pending_email, "")
        self.assertIsNone(self.user.pending_email_sent_at)

        again = mail_client.post(path)
        self.assertContains(again, 'id="email-confirm-invalid"')
        self.assertContains(Client().get(path), 'id="email-confirm-invalid"')
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, NEW_EMAIL)

    def test_a_post_without_its_csrf_token_is_refused(self) -> None:
        path = self.requested_link()
        strict_client = Client(enforce_csrf_checks=True)
        self.assertContains(strict_client.get(path), 'id="email-confirm-form"')
        response = strict_client.post(path)
        self.assertEqual(response.status_code, 403)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, OLD_EMAIL)
        self.assertEqual(self.user.pending_email, NEW_EMAIL)

    def test_an_expired_link_is_refused(self) -> None:
        path = self.requested_link()
        later = datetime.now() + timedelta(seconds=settings.PASSWORD_RESET_TIMEOUT + 60)
        with patch.object(EmailChangeTokenGenerator, "_now", return_value=later):
            response = Client().post(path)
        self.assertContains(response, 'id="email-confirm-invalid"')
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, OLD_EMAIL)

    def test_a_newer_request_replaces_the_older_link(self) -> None:
        """Even for the same address: the request's time is in the token."""
        first = self.requested_link()
        with patch(
            "accounts.email_change.timezone.now",
            return_value=self.user.date_joined + timedelta(minutes=5),
        ):
            second = self.requested_link()
        self.assertNotEqual(first, second)
        self.assertContains(Client().post(first), 'id="email-confirm-invalid"')
        self.assertContains(Client().post(second), 'id="email-confirmed"')
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, NEW_EMAIL)

    def test_a_password_change_kills_the_link(self) -> None:
        path = self.requested_link()
        self.user.set_password("SomethingElse42x")
        self.user.save()
        self.assertContains(Client().post(path), 'id="email-confirm-invalid"')

    def test_a_tampered_token_or_unknown_account_is_refused(self) -> None:
        path = self.requested_link()
        uidb64 = path.split("/")[-3]
        for bad in (
            f"/tarmar/accounts/password_reset/address/{uidb64}/1-abc/",
            f"/tarmar/accounts/password_reset/address/OTk5OQ/{path.split('/')[-2]}/",
            "/tarmar/accounts/password_reset/address/!!/x/",
        ):
            self.assertContains(Client().post(bad), 'id="email-confirm-invalid"')
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, OLD_EMAIL)


class ResetGoesToTheVerifiedAddressTest(EmailChangeTestCase):
    def reset(self, address: str) -> None:
        Client().post(RESET_URL, {"email": address})

    def test_an_unconfirmed_address_is_sent_no_reset(self) -> None:
        self.user.email_verified = False
        self.user.save()
        self.reset(OLD_EMAIL)
        self.assertEqual(mail.outbox, [])

    def test_a_pending_address_is_sent_no_reset_until_confirmed(self) -> None:
        path = self.requested_link()
        mail.outbox.clear()
        self.reset(NEW_EMAIL)
        self.assertEqual(mail.outbox, [])
        self.reset(OLD_EMAIL)
        self.assertEqual([message.to for message in mail.outbox], [[OLD_EMAIL]])

        Client().post(path)
        mail.outbox.clear()
        self.reset(OLD_EMAIL)
        self.assertEqual(mail.outbox, [])
        self.reset(NEW_EMAIL)
        self.assertEqual([message.to for message in mail.outbox], [[NEW_EMAIL]])


class LimitTest(EmailChangeTestCase):
    def test_requests_past_the_network_allowance_are_refused(self) -> None:
        for attempt in range(EmailChangeView.CHANGE_MAX_REQUESTS):
            response = self.request_change(password="wrong")
            self.assertEqual(response.status_code, 200, msg=f"attempt {attempt}")
        response = self.request_change()
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Too many email change requests", status_code=429)
        self.user.refresh_from_db()
        self.assertEqual(self.user.pending_email, "")

    def test_one_address_is_sent_at_most_its_share(self) -> None:
        for source in range(MAX_SENDS_PER_ADDRESS):
            self.request_change(network_address=f"192.0.2.{source + 1}")
        sent_to_new = [message for message in mail.outbox if message.to == [NEW_EMAIL]]
        self.assertEqual(len(sent_to_new), MAX_SENDS_PER_ADDRESS)
        pending_before = User.objects.get(pk=self.user.pk).pending_email_sent_at

        response = self.request_change(network_address="198.51.100.1")
        self.assertContains(response, "as many messages from this site")
        sent_to_new = [message for message in mail.outbox if message.to == [NEW_EMAIL]]
        self.assertEqual(len(sent_to_new), MAX_SENDS_PER_ADDRESS)
        self.assertEqual(
            User.objects.get(pk=self.user.pk).pending_email_sent_at, pending_before
        )

    def test_the_share_is_the_one_the_reset_form_spends(self) -> None:
        for source in range(MAX_SENDS_PER_ADDRESS):
            Client().post(
                RESET_URL, {"email": NEW_EMAIL}, REMOTE_ADDR=f"192.0.2.{source + 1}"
            )
        response = self.request_change()
        self.assertContains(response, "as many messages from this site")
        self.assertEqual(mail.outbox, [])


class RegistrationTest(TestCase):
    def test_a_registered_address_is_unconfirmed_until_its_link_is_opened(
        self,
    ) -> None:
        client = Client()
        client.post(
            "/tarmar/accounts/register/",
            {
                "username": "newcomer",
                "password1": "LanternBridge42x",
                "password2": "LanternBridge42x",
                "email": NEW_EMAIL,
                "preferred_contact": "email",
            },
        )
        account = User.objects.get(username="newcomer")
        self.assertEqual(account.email, NEW_EMAIL)
        self.assertFalse(account.email_verified)
        self.assertEqual(account.pending_email, NEW_EMAIL)

        path = link_in(message_to(NEW_EMAIL))
        Client().post(path)
        account.refresh_from_db()
        self.assertTrue(account.email_verified)
        self.assertEqual(account.pending_email, "")

    def test_registering_without_an_address_sends_nothing(self) -> None:
        Client().post(
            "/tarmar/accounts/register/",
            {
                "username": "quiet",
                "password1": "LanternBridge42x",
                "password2": "LanternBridge42x",
                "preferred_contact": "discord",
                "discord": "quiet_one",
            },
        )
        self.assertTrue(User.objects.filter(username="quiet").exists())
        self.assertEqual(mail.outbox, [])


class SetEmailVerifiedCommandTest(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user(
            username="spencer",
            password=TEST_PASSWORD,
            email=OLD_EMAIL,
            email_verified=True,
        )

    def run_command(self, *args: str) -> str:
        output = StringIO()
        call_command("set_email_verified", *args, stdout=output)
        return output.getvalue()

    def test_false_takes_the_address_off_the_reset(self) -> None:
        output = self.run_command("spencer", "false")
        self.assertIn("not confirmed", output)
        self.user.refresh_from_db()
        self.assertFalse(self.user.email_verified)
        Client().post(RESET_URL, {"email": OLD_EMAIL})
        self.assertEqual(mail.outbox, [])

    def test_true_puts_it_back(self) -> None:
        self.run_command("spencer", "false")
        self.run_command("spencer", "true")
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)

    def test_unknown_account_and_missing_address_are_refused(self) -> None:
        with self.assertRaisesMessage(CommandError, "No account with username"):
            self.run_command("nobody", "false")
        User.objects.create_user(username="blank", password=TEST_PASSWORD)
        with self.assertRaisesMessage(CommandError, "no email address"):
            self.run_command("blank", "true")


class MigrationMarksExistingAddressesTest(TransactionTestCase):
    """Migration 0012 keeps every address already on file a reset route."""

    available_apps = None
    BEFORE = "0011_add_phone_discord_validators"
    AFTER = "0012_user_email_verification"

    def migrate_to(self, target: str):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        state = executor.migrate([("accounts", target)])
        executor.loader.build_graph()
        return state

    def tearDown(self) -> None:
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        newest = [name for app, name in executor.loader.graph.leaf_nodes("accounts")]
        self.migrate_to(newest[0])
        super().tearDown()

    def test_existing_addresses_are_verified_and_blank_ones_are_not(self) -> None:
        before = self.migrate_to(self.BEFORE)
        user_then = before.apps.get_model("accounts", "User")
        user_then.objects.create(username="with_address", email=OLD_EMAIL)
        user_then.objects.create(username="without_address", email="")

        after = self.migrate_to(self.AFTER)
        user_now = after.apps.get_model("accounts", "User")
        self.assertTrue(user_now.objects.get(username="with_address").email_verified)
        self.assertFalse(
            user_now.objects.get(username="without_address").email_verified
        )
        self.assertEqual(
            user_now.objects.get(username="with_address").pending_email, ""
        )
