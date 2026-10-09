"""The shared notice, field-error and sign-in pieces (#338 items 9, 17 and 25)."""

from django import forms
from django.contrib.auth import get_user_model
from django.template import Context, Template
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse


class NoticePartialTest(SimpleTestCase):
    """templates/_notice.html renders the markup notify.js builds."""

    def test_an_error_notice_is_an_alert(self):
        html = render_to_string(
            "_notice.html", {"message": "The seed must be a number."}
        )

        self.assertInHTML(
            '<div class="notice notice-error" role="alert">'
            "The seed must be a number.</div>",
            html,
        )

    def test_another_kind_is_a_status(self):
        html = render_to_string(
            "_notice.html", {"message": "Saved.", "kind": "success"}
        )

        self.assertInHTML(
            '<div class="notice notice-success" role="status">Saved.</div>', html
        )

    def test_the_message_is_escaped(self):
        html = render_to_string("_notice.html", {"message": "<b>bold</b>"})

        self.assertIn("&lt;b&gt;bold&lt;/b&gt;", html)


class _OneFieldForm(forms.Form):
    name = forms.CharField()


class FieldErrorPartialTest(SimpleTestCase):
    """templates/_field_error.html shows a field's first error, or nothing."""

    def test_an_invalid_field_shows_its_first_error(self):
        form = _OneFieldForm(data={"name": ""})
        form.is_valid()

        html = render_to_string("_field_error.html", {"field": form["name"]})

        self.assertInHTML('<span class="error">This field is required.</span>', html)

    def test_a_valid_field_renders_nothing(self):
        form = _OneFieldForm(data={"name": "Morwen"})
        form.is_valid()

        html = render_to_string("_field_error.html", {"field": form["name"]})

        self.assertEqual(html.strip(), "")


class ContactSelectPartialTest(TestCase):
    """The preferred-contact select shared by register and profile."""

    def test_without_a_selection_no_option_is_marked(self):
        html = render_to_string("accounts/partials/contact_select.html", {})

        self.assertNotIn("selected", html)
        self.assertNotIn("autosave", html)
        self.assertIn('<option value="">---</option>', html)

    def test_the_stored_choice_is_marked_and_autosave_is_added(self):
        stored = get_user_model().CONTACT_CHOICES[0][0]

        html = render_to_string(
            "accounts/partials/contact_select.html",
            {"selected": stored, "autosave": True},
        )

        self.assertIn('class="autosave"', html)
        self.assertIn(f'<option value="{stored}" selected>', html)
        self.assertEqual(html.count(" selected>"), 1)


class SignInUrlTagTest(SimpleTestCase):
    """{% sign_in_url %} is the hand-written login?next= expression, once."""

    def render(self, path: str) -> tuple[str, str]:
        request = RequestFactory().get(path)
        context = Context({"request": request})
        tag = Template("{% load sign_in %}{% sign_in_url %}").render(context)
        by_hand = Template(
            "{% url 'login' %}?next={{ request.get_full_path|urlencode }}"
        ).render(context)
        return tag, by_hand

    def test_it_matches_the_expression_it_replaces(self):
        for path in (
            "/tarmar/battles/",
            "/tarmar/battles/setup/12/",
            "/tarmar/battles/?status=deleted&page=2",
            "/tarmar/battles/?q=a b&x=ä",
        ):
            with self.subTest(path=path):
                tag, by_hand = self.render(path)
                self.assertEqual(tag, by_hand)

    def test_it_points_at_login_and_comes_back_here(self):
        tag, _ = self.render("/tarmar/battles/7/")

        self.assertEqual(tag, f"{reverse('login')}?next=/tarmar/battles/7/")

    def test_without_a_request_it_is_the_plain_login_url(self):
        html = Template("{% load sign_in %}{% sign_in_url %}").render(Context({}))

        self.assertEqual(html, reverse("login"))
