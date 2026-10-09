"""The battle status labels have one source, the model (#338 item 20)."""

import json
import re
from pathlib import Path

from django.template import Context, Template
from django.test import SimpleTestCase

from battle.models import Battle

ROOT = Path(__file__).resolve().parents[2]


class BattleStatusLabelsTagTest(SimpleTestCase):
    def test_it_renders_the_model_labels_for_battle_js(self):
        html = Template("{% load battle_status %}{% battle_status_labels %}").render(
            Context({})
        )

        match = re.fullmatch(
            r'<script id="battle-status-labels" type="application/json">(.*)</script>',
            html,
        )
        assert match is not None, html
        self.assertEqual(json.loads(match.group(1)), dict(Battle.STATUS_CHOICES))

    def test_both_status_chips_are_given_the_labels(self):
        for template in (
            "battle/templates/battle/battle_detail.html",
            "gm/templates/gm/_panel_battle.html",
        ):
            with self.subTest(template=template):
                text = (ROOT / template).read_text()
                self.assertIn('id="battle-status"', text)
                self.assertIn("{% battle_status_labels %}", text)

    def test_battle_js_does_not_restate_a_label(self):
        script = (ROOT / "static" / "js" / "battle.js").read_text()
        for _code, label in Battle.STATUS_CHOICES:
            if " " in label:
                with self.subTest(label=label):
                    self.assertNotIn(f"'{label}'", script)
        self.assertNotIn("STATUS_LABELS", script)
