"""No template comment is rendered as text (issue #408).

Django's ``{# … #}`` comment is single-line only. A ``{#`` whose ``#}`` sits
on a later line is not a comment at all, so everything between is printed on
the page. Three of them reached users that way: the GM chat page showed a note
about its own status line, and the character create and list pages showed
notes too. Multi-line notes belong in ``{% comment %}…{% endcomment %}``.
"""

import tempfile
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from tarmar_studio.tests.repository_scan import SKIPPED_DIRECTORY_NAMES

PROJECT_ROOT = Path(settings.BASE_DIR)


def project_templates() -> list[Path]:
    """Every HTML template in the project, outside dependencies and build output."""
    return sorted(
        path
        for path in PROJECT_ROOT.rglob("*.html")
        if not SKIPPED_DIRECTORY_NAMES.intersection(
            path.relative_to(PROJECT_ROOT).parts
        )
    )


def unclosed_comments(path: Path) -> list[str]:
    """Lines in ``path`` that open ``{#`` without closing ``#}`` on the same line."""
    found = []
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        remainder = line
        while "{#" in remainder:
            after = remainder.split("{#", 1)[1]
            if "#}" not in after:
                shown = (
                    path.relative_to(PROJECT_ROOT)
                    if path.is_relative_to(PROJECT_ROOT)
                    else path
                )
                found.append(f"{shown}:{number}: {line.strip()}")
                break
            remainder = after.split("#}", 1)[1]
    return found


class TemplateCommentTest(SimpleTestCase):
    def test_the_scan_finds_templates(self):
        self.assertTrue(any(path.name == "chat.html" for path in project_templates()))

    def test_no_single_line_comment_runs_onto_another_line(self):
        offenders = [
            line for path in project_templates() for line in unclosed_comments(path)
        ]

        self.assertEqual(
            offenders,
            [],
            "Django's {# #} comments are single-line; use {% comment %} for these:\n"
            + "\n".join(offenders),
        )

    def test_the_scan_catches_the_shape_that_broke(self):
        # The sample lives outside the project: written into the tree, it was
        # a template the whole-tree scan above could read from another
        # worker while it existed, and fail on (the class of issue #747).
        with tempfile.TemporaryDirectory() as directory:
            sample = Path(directory) / "_comment_sample.html"
            sample.write_text("{# opened here\n   and closed here #}\n{# fine #}\n")
            self.assertEqual(len(unclosed_comments(sample)), 1)


class CommentsAreNotRenderedTest(SimpleTestCase):
    """The pages that showed the notes no longer do."""

    def test_the_gm_chat_page_hides_its_status_note(self):
        from django.template.loader import get_template

        source = Path(get_template("gm/chat.html").origin.name).read_text()

        self.assertNotIn("{# What the GM is doing", source)
