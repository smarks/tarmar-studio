"""The account pages' limits are the server's, and past them is a refusal (#943).

Register, sign-in, profile and the two password-reset pages typed their
``maxlength`` and ``minlength`` out as literal numbers. They now read
``account_text_limits`` (:data:`accounts.text_limits.ACCOUNT_TEXT_LIMITS`),
whose numbers are the user model's columns, the shared registration form's
own limits and the password validator's option. Each page is rendered with
the map swapped for numbers no template would carry by chance, so a literal
left in one fails here; and each path that stores a value refuses one
character past the limit.
"""

from html.parser import HTMLParser
from unittest import mock

from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm, PasswordResetForm
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.test import override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from origami_auth.forms import RegistrationForm as SharedRegistrationForm

from accounts.forms import EmailChangeForm
from accounts.models import User
from accounts.text_limits import ACCOUNT_TEXT_LIMITS, password_minimum
from accounts.views import EMAIL_FIELD_REFUSAL
from common.test_helpers import TEST_PASSWORD, BaseTestCase

#: Limits no template would carry by chance.
ODD_LIMITS = {
    "username": 23,
    "sign_in_username": 147,
    "password": 121,
    "password_min": 11,
    "real_name": 97,
    "email": 251,
    "phone": 19,
    "discord": 47,
}

#: A password every configured validator accepts.
GOOD_PASSWORD = "LanternBridge42x"


class _InputLimits(HTMLParser):
    """Each input's ``maxlength`` and ``minlength``, keyed by its id."""

    def __init__(self) -> None:
        super().__init__()
        self.found: dict[str, dict[str, str]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "input":
            return
        attributes = {key: value or "" for key, value in attrs}
        limits = {
            name: attributes[name]
            for name in ("maxlength", "minlength")
            if name in attributes
        }
        self.found[attributes.get("id", "")] = limits


def input_limits(html: str) -> dict[str, dict[str, str]]:
    """Every input's length attributes in ``html``; see :class:`_InputLimits`."""
    parser = _InputLimits()
    parser.feed(html)
    return parser.found


def configured_password_minimum() -> int:
    """The ``min_length`` option ``AUTH_PASSWORD_VALIDATORS`` sets, as written."""
    (option,) = [
        validator["OPTIONS"]["min_length"]
        for validator in settings.AUTH_PASSWORD_VALIDATORS
        if validator["NAME"].endswith(".MinimumLengthValidator")
    ]
    return option


def email_of_length(length: int) -> str:
    """A well-formed address exactly ``length`` characters long."""
    domain = "@example.com"
    return "a" * (length - len(domain)) + domain


class LimitsAreTheSourcesTest(BaseTestCase):
    """Each number in the map is the column, form or validator it names."""

    def column(self, field_name: str) -> int | None:
        return User._meta.get_field(field_name).max_length

    def test_each_limit_is_its_source(self):
        shared_form = SharedRegistrationForm()
        self.assertEqual(
            ACCOUNT_TEXT_LIMITS,
            {
                "username": shared_form.fields["username"].max_length,
                "sign_in_username": self.column("username"),
                "password": shared_form.fields["password1"].widget.attrs["maxlength"],
                "password_min": configured_password_minimum(),
                "real_name": self.column("real_name"),
                "email": self.column("email"),
                "phone": self.column("phone"),
                "discord": self.column("discord"),
            },
        )

    def test_the_forms_hold_the_same_numbers(self):
        shared_fields = SharedRegistrationForm().fields
        for field_name in ("real_name", "email", "phone", "discord"):
            with self.subTest(field=field_name):
                self.assertEqual(
                    shared_fields[field_name].max_length,
                    ACCOUNT_TEXT_LIMITS[field_name],
                )
        self.assertEqual(
            shared_fields["password2"].widget.attrs["maxlength"],
            ACCOUNT_TEXT_LIMITS["password"],
        )
        self.assertEqual(
            AuthenticationForm().fields["username"].max_length,
            ACCOUNT_TEXT_LIMITS["sign_in_username"],
        )
        self.assertEqual(
            PasswordResetForm().fields["email"].max_length,
            ACCOUNT_TEXT_LIMITS["email"],
        )
        self.assertEqual(
            EmailChangeForm(self.create_user()).fields["new_email"].max_length,
            ACCOUNT_TEXT_LIMITS["email"],
        )
        self.assertEqual(self.column("pending_email"), ACCOUNT_TEXT_LIMITS["email"])

    def test_the_minimum_is_read_from_the_validator_setting(self):
        validators = [
            {
                "NAME": "django.contrib.auth.password_validation."
                "MinimumLengthValidator",
                "OPTIONS": {"min_length": 13},
            }
        ]
        with override_settings(AUTH_PASSWORD_VALIDATORS=validators):
            self.assertEqual(password_minimum(), 13)

    def test_the_longest_of_several_minimums_is_the_one(self):
        name = "django.contrib.auth.password_validation.MinimumLengthValidator"
        validators = [
            {"NAME": name, "OPTIONS": {"min_length": 9}},
            {"NAME": name, "OPTIONS": {"min_length": 14}},
            {"NAME": name, "OPTIONS": {"min_length": 10}},
        ]
        with override_settings(AUTH_PASSWORD_VALIDATORS=validators):
            self.assertEqual(password_minimum(), 14)

    def test_no_minimum_configured_is_refused(self):
        with override_settings(AUTH_PASSWORD_VALIDATORS=[]):
            with self.assertRaises(ImproperlyConfigured):
                password_minimum()


class PagesReadTheMapTest(BaseTestCase):
    """Each page's inputs carry the map's numbers, real and swapped."""

    def rendered(self, url: str) -> dict[str, dict[str, str]]:
        page = self.client.get(url, follow=True)
        self.assertEqual(page.status_code, 200)
        return input_limits(page.content.decode())

    def assert_page_limits(self, url: str, expected: dict[str, dict[str, str]]):
        """``expected`` maps an input id to its attribute and the map's key."""
        for limits in (ACCOUNT_TEXT_LIMITS, ODD_LIMITS):
            with mock.patch.dict(ACCOUNT_TEXT_LIMITS, limits):
                found = self.rendered(url)
            for input_id, attributes in expected.items():
                with self.subTest(url=url, input=input_id, limits=limits["email"]):
                    self.assertEqual(
                        found.get(input_id),
                        {
                            attribute: str(limits[key])
                            for attribute, key in attributes.items()
                        },
                    )

    def test_register(self):
        self.assert_page_limits(
            reverse("register"),
            {
                "id_username": {"maxlength": "username"},
                "id_password1": {
                    "maxlength": "password",
                    "minlength": "password_min",
                },
                "id_password2": {"maxlength": "password"},
                "id_real_name": {"maxlength": "real_name"},
                "id_email": {"maxlength": "email"},
                "id_phone": {"maxlength": "phone"},
                "id_discord": {"maxlength": "discord"},
            },
        )

    def test_the_password_hint_states_the_minimum(self):
        with mock.patch.dict(ACCOUNT_TEXT_LIMITS, ODD_LIMITS):
            page = self.client.get(reverse("register"))
        self.assertContains(page, "At least 11 characters")

    def test_login(self):
        self.assert_page_limits(
            reverse("login"),
            {
                "id_username": {"maxlength": "sign_in_username"},
                # The server never held a sign-in password to a length, so the
                # page stops none. A long one costs little: the default PBKDF2
                # hasher hashes it once to a fixed-size key before iterating,
                # DATA_UPLOAD_MAX_MEMORY_SIZE bounds the request body, and the
                # failed-sign-in limiter charges before the password is checked.
                "id_password": {},
            },
        )

    def test_profile(self):
        self.login_as(self.create_player(username="limits-profile"))
        self.assert_page_limits(
            reverse("profile"),
            {
                "id_real_name": {"maxlength": "real_name"},
                "id_email": {"maxlength": "email"},
                "id_phone": {"maxlength": "phone"},
                "id_discord": {"maxlength": "discord"},
            },
        )

    def test_email_change(self):
        self.login_as(self.create_player(username="limits-change"))
        self.assert_page_limits(
            reverse("email_change"),
            {
                "id_new_email": {"maxlength": "email"},
                # The account's own password, checked and never stored: as on
                # the sign-in page, nothing holds it to a length.
                "id_current_password": {},
            },
        )

    def test_password_reset_form(self):
        self.assert_page_limits(
            reverse("password_reset"), {"id_email": {"maxlength": "email"}}
        )

    def test_password_reset_confirm(self):
        user = self.create_player(username="limits-reset")
        link = reverse(
            "password_reset_confirm",
            args=[
                urlsafe_base64_encode(force_bytes(user.pk)),
                default_token_generator.make_token(user),
            ],
        )
        self.assert_page_limits(
            link,
            {
                "id_new_password1": {
                    "maxlength": "password",
                    "minlength": "password_min",
                },
                "id_new_password2": {"maxlength": "password"},
            },
        )


class RegistrationRefusalsTest(BaseTestCase):
    """Registration takes each value at its limit and refuses one more."""

    def register(self, username: str = "limits-new", **overrides: str):
        body = {
            "username": username,
            "password1": GOOD_PASSWORD,
            "password2": GOOD_PASSWORD,
            **overrides,
        }
        return self.client.post(reverse("register"), body)

    def values_of_length(self, field_name: str, length: int) -> dict[str, str]:
        if field_name == "password":
            password = "Lb4" + "x" * (length - 3)
            return {"password1": password, "password2": password}
        if field_name == "email":
            return {"email": email_of_length(length)}
        if field_name == "phone":
            return {"phone": "+" + "1" * (length - 1)}
        return {field_name: "a" * length}

    def test_one_past_each_limit_is_refused(self):
        for field_name in ("password", "real_name", "email", "phone", "discord"):
            limit = ACCOUNT_TEXT_LIMITS[field_name]
            with self.subTest(field=field_name):
                response = self.register(**self.values_of_length(field_name, limit + 1))
                self.assertEqual(response.status_code, 200)
                self.assertFalse(User.objects.filter(username="limits-new").exists())

    def test_each_limit_itself_is_taken(self):
        # Discord's own validator refuses past 32 characters, inside its
        # column, so its column limit is not a value an account can hold.
        for field_name in ("password", "real_name", "email", "phone"):
            limit = ACCOUNT_TEXT_LIMITS[field_name]
            username = f"limits-{field_name}".replace("_", "-")
            with self.subTest(field=field_name):
                response = self.register(
                    username, **self.values_of_length(field_name, limit)
                )
                self.assertEqual(response.status_code, 302)
                self.assertTrue(User.objects.filter(username=username).exists())
                self.logout()

    def test_the_username_limit(self):
        limit = ACCOUNT_TEXT_LIMITS["username"]
        refused = self.register("u" * (limit + 1))
        self.assertEqual(refused.status_code, 200)
        self.assertContains(refused, f"Username must be {limit} characters or fewer")
        self.assertEqual(self.register("u" * limit).status_code, 302)

    def test_the_password_minimum(self):
        minimum = ACCOUNT_TEXT_LIMITS["password_min"]
        refused = self.register(**self.values_of_length("password", minimum - 1))
        self.assertEqual(refused.status_code, 200)
        self.assertContains(refused, f"at least {minimum} characters")
        self.assertFalse(User.objects.filter(username="limits-new").exists())


class ProfileRefusalsTest(BaseTestCase):
    """The profile endpoint refuses one character past each column it writes.

    The address is not one of them: it changes only on its own page (#931),
    whose refusal is :class:`EmailChangeRefusalsTest`.
    """

    def test_one_past_each_column_is_refused_and_nothing_is_saved(self):
        user = self.create_player(username="limits-edit")
        self.login_as(user)
        too_long = {
            "real_name": "a" * (ACCOUNT_TEXT_LIMITS["real_name"] + 1),
            "phone": "+" + "1" * ACCOUNT_TEXT_LIMITS["phone"],
            "discord": "a" * (ACCOUNT_TEXT_LIMITS["discord"] + 1),
        }
        for field_name, value in too_long.items():
            with self.subTest(field=field_name):
                response = self.post_json(
                    reverse("profile_update_field"),
                    {"field": field_name, "value": value},
                )
                self.assertEqual(response.status_code, 400)
                user.refresh_from_db()
                self.assertEqual(getattr(user, field_name), "")
        response = self.post_json(
            reverse("profile_update_field"),
            {"field": "real_name", "value": too_long["real_name"]},
        )
        limit = ACCOUNT_TEXT_LIMITS["real_name"]
        self.assertIn(f"at most {limit} characters", response.json()["error"])

    def test_the_address_is_refused_at_any_length(self):
        user = self.create_player(username="limits-edit-email")
        self.login_as(user)
        response = self.post_json(
            reverse("profile_update_field"),
            {"field": "email", "value": email_of_length(30)},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], EMAIL_FIELD_REFUSAL)
        user.refresh_from_db()
        self.assertEqual(user.email, "")


class EmailChangeRefusalsTest(BaseTestCase):
    """The email-change form takes the column's length and refuses one more."""

    def setUp(self):
        super().setUp()
        self.user = self.create_player(username="limits-mover")
        self.login_as(self.user)

    def change_to(self, address: str):
        return self.client.post(
            reverse("email_change"),
            {"new_email": address, "current_password": TEST_PASSWORD},
        )

    def test_one_past_the_column_is_refused_and_nothing_is_sent(self):
        limit = ACCOUNT_TEXT_LIMITS["email"]
        response = self.change_to(email_of_length(limit + 1))
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            f"at most {limit} characters",
            response.context["form"].errors["new_email"][0],
        )
        self.user.refresh_from_db()
        self.assertFalse(self.user.pending_email)
        self.assertEqual(mail.outbox, [])

    def test_the_column_itself_is_taken(self):
        address = email_of_length(ACCOUNT_TEXT_LIMITS["email"])
        response = self.change_to(address)
        self.assertRedirects(response, reverse("profile"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.pending_email, address)


class AdminSetsAddressLimitTest(BaseTestCase):
    """An Admin's address for another account is held to the column (#954)."""

    def setUp(self):
        super().setUp()
        self.user = self.create_player(username="limits-target")
        self.login_as(self.create_admin(username="limits-admin"))

    def set_to(self, address: str):
        return self.post_json(
            reverse("user_profile_update_field", args=[self.user.pk]),
            {"field": "email", "value": address},
        )

    def test_one_past_the_column_is_refused_and_nothing_is_sent(self):
        limit = ACCOUNT_TEXT_LIMITS["email"]
        response = self.set_to(email_of_length(limit + 1))
        self.assertEqual(response.status_code, 400)
        self.assertIn(f"at most {limit} characters", response.json()["error"])
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "")
        self.assertEqual(mail.outbox, [])

    def test_the_column_itself_is_taken(self):
        address = email_of_length(ACCOUNT_TEXT_LIMITS["email"])
        response = self.set_to(address)
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, address)
        self.assertFalse(self.user.email_verified)


class SignInLimitTest(BaseTestCase):
    def test_a_name_longer_than_registration_allows_still_signs_in(self):
        username = "o" * (ACCOUNT_TEXT_LIMITS["username"] + 5)
        self.create_user(username=username)
        self.logout()
        response = self.client.post(
            reverse("login"), {"username": username, "password": TEST_PASSWORD}
        )
        self.assertEqual(response.status_code, 302)

    def test_one_past_the_column_can_be_neither_held_nor_signed_in_with(self):
        # Django's sign-in form states the column as its field's max_length
        # but runs no validator on it, so the sign-in itself refuses nothing
        # by length. The column is what refuses, when an account is saved, so
        # no account has a longer name and the page's maxlength turns nobody
        # away.
        limit = ACCOUNT_TEXT_LIMITS["sign_in_username"]
        account = User(username="o" * (limit + 1))
        account.set_password(TEST_PASSWORD)
        with self.assertRaises(ValidationError) as refusal:
            account.full_clean()
        self.assertIn(
            f"at most {limit} characters", refusal.exception.message_dict["username"][0]
        )
        response = self.client.post(
            reverse("login"),
            {"username": "o" * (limit + 1), "password": TEST_PASSWORD},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.wsgi_request.user.is_authenticated)


class PasswordResetRefusalsTest(BaseTestCase):
    """The reset pages refuse what registration refuses."""

    def setUp(self):
        super().setUp()
        self.user = self.create_player(username="limits-forgot")

    def set_password_url(self) -> str:
        link = reverse(
            "password_reset_confirm",
            args=[
                urlsafe_base64_encode(force_bytes(self.user.pk)),
                default_token_generator.make_token(self.user),
            ],
        )
        return self.client.get(link)["Location"]

    def set_password(self, password: str):
        return self.client.post(
            self.set_password_url(),
            {"new_password1": password, "new_password2": password},
        )

    def test_one_past_the_password_limit_is_refused_in_registrations_words(self):
        for limits in (ACCOUNT_TEXT_LIMITS, ODD_LIMITS):
            limit = limits["password"]
            with (
                self.subTest(limit=limit),
                mock.patch.dict(ACCOUNT_TEXT_LIMITS, limits),
            ):
                response = self.set_password("Lb4" + "x" * (limit - 2))
                self.assertEqual(response.status_code, 200)
                self.assertContains(
                    response, f"Password must be {limit} characters or fewer"
                )
                self.user.refresh_from_db()
                self.assertTrue(self.user.check_password(TEST_PASSWORD))

    def test_the_limit_itself_is_taken(self):
        password = "Lb4" + "x" * (ACCOUNT_TEXT_LIMITS["password"] - 3)
        response = self.set_password(password)
        self.assertRedirects(response, reverse("password_reset_complete"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(password))

    def test_one_short_of_the_minimum_is_refused(self):
        minimum = ACCOUNT_TEXT_LIMITS["password_min"]
        response = self.set_password("Lb4" + "x" * (minimum - 4))
        self.assertContains(response, f"at least {minimum} characters")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(TEST_PASSWORD))

    def test_an_address_past_the_column_is_refused_and_nothing_is_sent(self):
        limit = ACCOUNT_TEXT_LIMITS["email"]
        response = self.client.post(
            reverse("password_reset"), {"email": email_of_length(limit + 1)}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("email", response.context["form"].errors)
        self.assertEqual(mail.outbox, [])
