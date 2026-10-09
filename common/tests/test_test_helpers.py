"""Tests for the shared test factories and session helpers."""

import tempfile
import unittest
from pathlib import Path

from django.test import Client, SimpleTestCase, TestCase

from bestiary.models import Beast
from common.middleware import PublicPathPassphraseMiddleware
from common.test_helpers import (
    BEAST_DEFAULTS,
    TEST_PASSWORD,
    BaseTestCase,
    make_beast,
    mark_gate_passed,
    skip_unless_canon_expects,
)


class MakeBeastTest(TestCase):
    def test_saves_a_beast_from_the_defaults(self):
        beast = make_beast(slug="helper-wolf")
        self.assertIsNotNone(beast.pk)
        self.assertEqual(beast.name, BEAST_DEFAULTS["name"])
        self.assertEqual(beast.strength, BEAST_DEFAULTS["strength"])

    def test_overrides_win_and_unsaved_is_not_stored(self):
        beast = make_beast(save=False, slug="unsaved-wolf", strength=20)
        self.assertIsNone(beast.pk)
        self.assertEqual(beast.strength, 20)
        self.assertFalse(Beast.objects.filter(slug="unsaved-wolf").exists())

    def test_attacks_are_copied_so_tests_cannot_leak_edits(self):
        beast = make_beast(save=False)
        beast.attacks[0]["damage"] = "9d6"
        self.assertEqual(BEAST_DEFAULTS["attacks"][0]["damage"], "1d6+1")


class MarkGatePassedTest(TestCase):
    def test_sets_the_gate_session_flag(self):
        client = Client()
        mark_gate_passed(client)
        self.assertTrue(client.session[PublicPathPassphraseMiddleware.SESSION_FLAG])


class LoginDmTest(BaseTestCase):
    def test_logs_in_a_dm_with_the_shared_password(self):
        dm = self.login_dm()
        self.assertTrue(dm.is_dm)
        self.assertTrue(dm.check_password(TEST_PASSWORD))
        self.assertEqual(int(self.client.session["_auth_user_id"]), dm.pk)


class SkipUnlessCanonExpectsTest(SimpleTestCase):
    """Skip without the expectations file; fail when it lacks the key (#922)."""

    def setUp(self) -> None:
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.expectations_file = directory / "expectations.json"

    def run_decorated(self, *, on_class: bool) -> unittest.TestResult:
        """Run one trivial test under the decorator, on a method or a class."""
        decorator = skip_unless_canon_expects("names", self.expectations_file)

        def test_the_names(test_case: unittest.TestCase) -> None:
            test_case.assertTrue(True)

        if on_class:
            case = decorator(
                type("Case", (unittest.TestCase,), {"test_it": test_the_names})
            )
        else:
            case = type(
                "Case", (unittest.TestCase,), {"test_it": decorator(test_the_names)}
            )
        result = unittest.TestResult()
        case("test_it").run(result)
        return result

    def test_no_file_skips(self) -> None:
        for on_class in (False, True):
            with self.subTest(on_class=on_class):
                result = self.run_decorated(on_class=on_class)
                self.assertEqual(len(result.skipped), 1)
                self.assertEqual(result.failures, [])

    def test_a_file_with_the_key_runs_the_test(self) -> None:
        self.expectations_file.write_text('{"names": ["Aster"]}', encoding="utf-8")
        for on_class in (False, True):
            with self.subTest(on_class=on_class):
                result = self.run_decorated(on_class=on_class)
                self.assertTrue(result.wasSuccessful())
                self.assertEqual(result.skipped, [])
                self.assertEqual(result.testsRun, 1)

    def test_a_file_without_the_key_fails_the_test(self) -> None:
        self.expectations_file.write_text('{"other": []}', encoding="utf-8")
        for on_class in (False, True):
            with self.subTest(on_class=on_class):
                result = self.run_decorated(on_class=on_class)
                self.assertEqual(result.skipped, [])
                self.assertEqual(len(result.failures), 1)
                self.assertIn("supplies no 'names'", result.failures[0][1])
