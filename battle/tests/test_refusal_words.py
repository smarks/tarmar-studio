"""The battle app's refusals are configuration, not code (issues #97, #86).

Every sentence a battle service, view or control module refuses a request in
is read from ``config/battle-refusals.toml`` (``settings.BATTLE_REFUSAL_WORDS``)
at startup. The shipped file is Spencer's to edit, so these tests pin no
sentence of it: they run under fixed words in which each sentence is its own
table and key (``common.wording.fixed_wording``), and prove the code answers in
whatever the configuration says. Changing the words must not change who is
refused, so the rule's two sides are checked under the fixed words as well.
"""

import tempfile
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.template.loader import render_to_string
from django.test import SimpleTestCase
from django.urls import reverse

from battle import control
from battle.access import Actor
from battle.models import BattleDraft
from battle.refusal_words import load_refusal_words
from battle.services import (
    add_draft_entry,
    create_battle,
    resnapshot_pools,
)
from battle.tests.fixed_refusals import FIXED_REFUSALS, with_fixed_refusals
from battle.views import arena_context
from common.test_helpers import BaseTestCase, create_character
from common.tests.refusal_scan import literal_refusals

SHIPPED = Path(settings.BASE_DIR) / "config" / "battle-refusals.toml"


#: The calls whose string arguments a player reads, by module. A literal
#: passed to one of them is a refusal written in code (issue #97).
REFUSING_CALLS = {
    "battle/services.py": {"BattleRefusal", "DraftFieldError", "StalePools"},
    "battle/views.py": {
        "BattleRefusal",
        "_json_error",
        "_refuse",
        "_setup_context",
        "_detail_with_error",
    },
    "battle/control.py": {"BattleRefusal"},
}


class ShippedRefusalWordsTest(SimpleTestCase):
    """The shipped file loads, and a broken one stops the server by name."""

    def test_the_shipped_file_loads_and_settings_hold_the_named_file(self):
        """The shipped file passes every check; the settings hold whatever file
        ``BATTLE_REFUSAL_WORDS_FILE`` names, which a deployment may change."""
        load_refusal_words(SHIPPED)
        self.assertEqual(
            load_refusal_words(settings.BATTLE_REFUSAL_WORDS_FILE),
            settings.BATTLE_REFUSAL_WORDS,
        )

    def test_a_missing_key_is_refused_by_the_setting_and_the_key(self):
        text = SHIPPED.read_text(encoding="utf-8")
        broken = "\n".join(
            line
            for line in text.splitlines()
            if not line.startswith("take_seat_game_master_only =")
        )
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "refusals.toml"
            path.write_text(broken, encoding="utf-8")
            with self.assertRaisesMessage(
                ImproperlyConfigured,
                f"BATTLE_REFUSAL_WORDS_FILE {path} [services]: unknown keys [], "
                "missing keys ['take_seat_game_master_only']",
            ):
                load_refusal_words(path)


class NoRefusalWrittenInCodeTest(SimpleTestCase):
    """No refusing call in the battle app is handed a sentence of its own."""

    def test_every_refusal_reads_the_configuration(self):
        for module, calls in REFUSING_CALLS.items():
            with self.subTest(module=module):
                self.assertEqual(
                    literal_refusals(
                        Path(settings.BASE_DIR) / module,
                        calls,
                        structure_keywords=frozenset({"field", "error_field"}),
                    ),
                    [],
                )


@with_fixed_refusals
class RefusalsFollowTheConfigurationTest(BaseTestCase):
    """Services, views, control and templates answer in the configured words."""

    def setUp(self):
        super().setUp()
        self.game_master = self.create_dm(username="words-gm")
        self.player = self.create_player(username="words-player")

    def test_a_service_refuses_in_the_configured_words(self):
        draft = BattleDraft.objects.create(created_by=self.game_master, name="Draft")
        character = create_character(self.game_master, name="Morwen")
        add_draft_entry(
            draft, self.game_master, kind="character", character_id=character.pk
        )
        with self.assertRaisesMessage(ValueError, "services.already_on_muster Morwen"):
            add_draft_entry(
                draft, self.game_master, kind="character", character_id=character.pk
            )

    def test_a_view_refuses_in_the_configured_words(self):
        battle = create_battle(self.game_master, name="Pit", count=2, seed=1)
        self.login_as(self.game_master)
        response = self.client.get(
            reverse("battle:state", args=[battle.pk]), {"after": "soon"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "views.numeric_after_marker")

    def test_control_refuses_in_the_configured_words(self):
        with self.assertRaisesMessage(ValueError, "control.battle_mode"):
            control.clean_mode("sometimes")
        with self.assertRaisesMessage(ValueError, "control.combatant_mode"):
            control.clean_combatant_mode("sometimes")

    def test_a_guest_reads_the_configured_note(self):
        self.client.logout()
        response = self.client.get(reverse("battle:list"))
        self.assertContains(response, "guest_notes.keep")

    def test_the_arena_gives_the_restart_button_the_configured_refusal(self):
        battle = create_battle(self.game_master, name="Pit", count=2, seed=1)
        context = arena_context(battle, Actor(user=self.game_master, guest_key=""))
        self.assertEqual(
            context["no_starting_snapshot"], "services.no_starting_snapshot"
        )

    def test_the_disabled_restart_button_says_the_configured_refusal(self):
        battle = create_battle(self.game_master, name="Pit", count=2, seed=1)
        page = render_to_string(
            "battle/_arena_restart.html",
            {
                "battle": battle,
                "can_restart": False,
                "no_starting_snapshot": FIXED_REFUSALS.services.no_starting_snapshot,
            },
        )
        self.assertIn('title="services.no_starting_snapshot"', page)

    def test_changing_the_words_changes_nobody_who_is_refused(self):
        """Taking a fight's pools from the records is a Game Master's ruling
        (#842): under any words a player is refused and a Game Master is not."""
        lobby = create_battle(
            self.game_master, name="Lobby", count=1, seed=4, player_seats=2
        )
        with self.assertRaisesMessage(
            ValueError, "services.resnapshot_game_master_only"
        ):
            resnapshot_pools(lobby.pk, is_game_master=False)
        # A Game Master passes the rule and meets the guard behind it: the
        # lobby has no fight to take pools into yet.
        with self.assertRaisesMessage(ValueError, "services.not_started"):
            resnapshot_pools(lobby.pk, is_game_master=True)
