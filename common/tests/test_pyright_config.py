"""The pyright gate has to cover every app (issue #198).

``battle`` and ``bestiary`` were missing from ``pyrightconfig.json``'s
include list, so the pre-commit and CI type check silently skipped both for
as long as they had existed. Nothing failed — the gate simply never looked.
This test is the drift guard: add a Django app and the type checker gains it
too, or this fails and says which one was forgotten.
"""

import json
from pathlib import Path

from django.apps import apps
from django.test import SimpleTestCase

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Apps that live outside this repo (installed packages) or carry no Python of
# ours to check.
EXTERNAL_APP_LABELS = frozenset(
    {"admin", "auth", "contenttypes", "sessions", "staticfiles"}
)


class PyrightIncludesEveryAppTest(SimpleTestCase):
    def setUp(self):
        self.config = json.loads((BASE_DIR / "pyrightconfig.json").read_text())

    def test_every_first_party_app_is_type_checked(self):
        included = set(self.config["include"])
        missing = sorted(
            app.label
            for app in apps.get_app_configs()
            if app.label not in EXTERNAL_APP_LABELS
            and Path(app.path).parent == BASE_DIR
            and app.label not in included
        )
        self.assertEqual(
            missing,
            [],
            "pyrightconfig.json's include list is missing these apps, so the "
            "type check silently skips them: " + ", ".join(missing),
        )

    def test_every_included_path_exists(self):
        absent = [
            name for name in self.config["include"] if not (BASE_DIR / name).is_dir()
        ]
        self.assertEqual(
            absent, [], f"pyrightconfig.json includes missing paths: {absent}"
        )
