"""Player-facing messages the environment may reword (issue #64)."""

from django.test import SimpleTestCase

from common.message_templates import (
    MessageTemplateError,
    message_template_from_environment,
)

DEFAULT = "Waited {seconds}s"


class MessageTemplateFromEnvironmentTest(SimpleTestCase):
    def test_unset_or_empty_means_the_default(self):
        for environ in ({}, {"GM_EXAMPLE": ""}):
            with self.subTest(environ=environ):
                self.assertEqual(
                    message_template_from_environment(
                        environ, "GM_EXAMPLE", DEFAULT, "seconds"
                    ),
                    DEFAULT,
                )

    def test_the_environment_rewords_it(self):
        environ = {"GM_EXAMPLE": "Held {seconds} seconds"}

        self.assertEqual(
            message_template_from_environment(
                environ, "GM_EXAMPLE", DEFAULT, "seconds"
            ),
            "Held {seconds} seconds",
        )

    def test_a_template_may_leave_a_field_out(self):
        environ = {"GM_EXAMPLE": "Held a while"}

        self.assertEqual(
            message_template_from_environment(
                environ, "GM_EXAMPLE", DEFAULT, "seconds"
            ),
            "Held a while",
        )

    def test_a_field_it_may_not_use_is_refused(self):
        environ = {"GM_EXAMPLE": "Held {minutes} minutes"}

        with self.assertRaisesMessage(
            MessageTemplateError,
            "GM_EXAMPLE names {minutes}; the fields it may use are {seconds}.",
        ):
            message_template_from_environment(environ, "GM_EXAMPLE", DEFAULT, "seconds")

    def test_a_message_with_no_fields_refuses_any(self):
        with self.assertRaisesMessage(
            MessageTemplateError, "the fields it may use are none."
        ):
            message_template_from_environment({}, "GM_EXAMPLE", "Held {seconds}")

    def test_a_stray_brace_is_refused(self):
        environ = {"GM_EXAMPLE": "Held {seconds"}

        with self.assertRaisesMessage(MessageTemplateError, "not a valid template"):
            message_template_from_environment(environ, "GM_EXAMPLE", DEFAULT, "seconds")

    def test_a_spec_or_conversion_a_number_cannot_take_is_refused(self):
        for template in ("Held {seconds:q}", "Held {seconds!z}"):
            with self.subTest(template=template):
                with self.assertRaisesMessage(
                    MessageTemplateError, "cannot be filled in"
                ):
                    message_template_from_environment(
                        {"GM_EXAMPLE": template}, "GM_EXAMPLE", DEFAULT, "seconds"
                    )

    def test_a_field_nested_in_a_spec_it_may_not_use_is_refused(self):
        environ = {"GM_EXAMPLE": "Held {seconds:{minutes}}"}

        with self.assertRaisesMessage(MessageTemplateError, "cannot be filled in"):
            message_template_from_environment(environ, "GM_EXAMPLE", DEFAULT, "seconds")

    def test_a_doubled_brace_is_a_brace(self):
        environ = {"GM_EXAMPLE": "Held {{a while}}"}

        configured = message_template_from_environment(environ, "GM_EXAMPLE", DEFAULT)

        self.assertEqual(configured.format(), "Held {a while}")
