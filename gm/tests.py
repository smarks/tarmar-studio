"""Tests for the GM app — model, services, and views."""

import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from unittest import skipUnless
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase, override_settings

from common.services import ollama_client
from common.test_helpers import (
    CANON_EXPECTATIONS,
    BaseTestCase,
    skip_unless_canon_expects,
)
from reference.paths import CANON_NAME, TARMAR_CANON_NAME

from . import views
from .models import GMSession
from .services import (
    build_roll_message,
    build_rules_lookup_message,
    build_state_request_message,
    get_system_message,
    load_rules,
    roll_dice,
)
from .test_magic_prompt import MAGIC_HEADINGS
from .testing import gm_replies

REPO_ROOT = Path(__file__).resolve().parent.parent


def _expected_fumble_bands(table):
    """The text each ``FUMBLE_TABLE`` band should appear as in either prompt.

    Spelled out here rather than imported from ``gm.engine`` on purpose: a
    guard that calls the renderer it is guarding proves nothing. (It used to
    also check the static ``cli/Modelfile``, the second copy that could
    drift; issue #324 removed that copy, so now it guards the one prompt.)
    """
    bands = []
    low = 1
    for threshold, _key, label, effect in table:
        span = str(low) if low == threshold else f"{low}–{threshold}"
        detail = f" ({effect.lower()})" if effect else ""
        bands.append(f"{span} {label.lower()}{detail}")
        low = threshold + 1
    return bands


# ---------- Model tests ----------


class GMSessionModelTest(TestCase):
    def test_create_session(self):
        """Session can be created with a name."""
        s = GMSession.objects.create(name="Test Session")
        self.assertEqual(s.name, "Test Session")
        self.assertEqual(s.history, [])
        self.assertEqual(s.message_count, 0)

    def test_history_property(self):
        """History property serializes/deserializes JSON."""
        s = GMSession.objects.create(name="History Test")
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]
        s.history = messages
        s.save()
        s.refresh_from_db()
        self.assertEqual(s.history, messages)

    def test_message_count_excludes_system(self):
        """message_count excludes system messages."""
        s = GMSession.objects.create(name="Count Test")
        s.history = [
            {"role": "system", "content": "System prompt"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]
        s.save()
        self.assertEqual(s.message_count, 2)

    def test_message_count_excludes_canon_entries(self):
        """Persisted canon excerpt blocks (issue #331) are not reader-visible turns."""
        s = GMSession.objects.create(name="Canon Count Test")
        s.history = [
            {
                "role": "user",
                "content": "excerpts",
                "kind": "canon",
                "chunk_ids": ["a"],
            },
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]
        s.save()
        self.assertEqual(s.message_count, 2)
        self.assertIn("(2 messages)", str(s))

    def test_str_representation(self):
        """String representation shows name and count."""
        s = GMSession.objects.create(name="Str Test")
        self.assertIn("Str Test", str(s))

    def test_ordering(self):
        """Sessions are ordered by most recently updated."""
        s1 = GMSession.objects.create(name="First")
        s2 = GMSession.objects.create(name="Second")
        sessions = list(GMSession.objects.all())
        self.assertEqual(sessions[0], s2)
        self.assertEqual(sessions[1], s1)


# ---------- Service tests ----------


class GMServiceTest(TestCase):
    def test_get_system_message(self):
        """System message has correct role and contains Tarmar info."""
        msg = get_system_message()
        self.assertEqual(msg["role"], "system")
        self.assertIn("Tarmar", msg["content"])

    def test_roll_dice_basic(self):
        """Basic dice roll returns correct count and range."""
        rolls, total = roll_dice("3d6")
        self.assertEqual(len(rolls), 3)
        self.assertTrue(all(1 <= r <= 6 for r in rolls))
        self.assertEqual(total, sum(rolls))

    def test_roll_dice_with_modifier(self):
        """Dice roll with modifier adds correctly."""
        rolls, total = roll_dice("1d6+5")
        self.assertEqual(len(rolls), 1)
        self.assertEqual(total, rolls[0] + 5)

    def test_roll_dice_negative_modifier(self):
        """Dice roll with negative modifier."""
        rolls, total = roll_dice("2d6-3")
        self.assertEqual(total, sum(rolls) - 3)

    def test_roll_dice_invalid_expression(self):
        """Invalid dice expression raises ValueError."""
        with self.assertRaises(ValueError):
            roll_dice("invalid")

    def test_roll_dice_rejects_out_of_range(self):
        """Huge counts/sides are rejected (DoS guard)."""
        with self.assertRaises(ValueError):
            roll_dice("999999999d6")
        with self.assertRaises(ValueError):
            roll_dice("1d999999999")

    def test_system_prompt_modifier_table_matches_code(self):
        """The GM prompt's modifier table must not drift from the code."""
        from characters.skills import calculate_attribute_modifier
        from gm.engine import system_prompt

        for value in (3, 4, 5, 6, 7):
            mod = calculate_attribute_modifier(value)
            self.assertIn(f"{value}=−{abs(mod)}", system_prompt())  # unicode minus
        self.assertIn("8-13=0", system_prompt())
        for value in (14, 15, 16, 17, 18):
            mod = calculate_attribute_modifier(value)
            self.assertIn(f"{value}=+{mod}", system_prompt())

    def test_system_prompt_lists_all_tracks(self):
        """Every experience track appears in the GM prompt (drift guard)."""
        from characters.skills import track_keys
        from gm.engine import system_prompt

        prompt_lower = system_prompt().lower()
        self.assertEqual(len(track_keys()), 8)
        for key in track_keys():
            self.assertIn(key.split("_")[0], prompt_lower)

    def test_system_prompt_combat_matrix_matches_code(self):
        """The GM prompt's d20 weapon-vs-armour matrix must not drift from code."""
        from characters.combat import ARMOUR_TIERS, MATRIX
        from gm.engine import system_prompt

        for weapon_class, row in MATRIX.items():
            numbers = "/".join(str(row[tier]) for tier in ARMOUR_TIERS)
            self.assertIn(f"{weapon_class} {numbers}", system_prompt())

    def test_system_prompt_fumble_ranges_match_code(self):
        """The prompt must print the actual 1d6 fumble bands (issue #105).

        It used to abbreviate the subtable to "off-balance, or drop/break
        weapon" — ranges and effects dropped — so the model could not roll on
        it. The bands are rendered from ``combat.FUMBLE_TABLE``; this locks
        the range, the label and the effect of each one.
        """
        from characters.combat import FUMBLE_TABLE
        from gm.engine import system_prompt

        for band in _expected_fumble_bands(FUMBLE_TABLE):
            with self.subTest(band=band):
                self.assertIn(band, system_prompt())
        self.assertIn("roll 1d6", system_prompt())

    def test_cli_has_no_second_copy_of_the_prompt(self):
        """The offline CLI sends the engine prompt itself, not a baked copy.

        Until issue #324 ``cli/Modelfile`` carried a second, hand-maintained
        prompt inside a custom ``tarmar-gm`` model, and two tests here checked
        its fumble bands and crit multipliers stayed in step with the code.
        The copy is gone: the CLI's system message *is* ``gm.engine``'s
        string, so the drift guards above cover both clients at once.
        """
        from cli.gm import build_system_message
        from gm.engine import system_prompt

        self.assertFalse((REPO_ROOT / "cli" / "Modelfile").exists())
        self.assertIs(
            build_system_message("unused rules text")["content"], system_prompt()
        )

    def test_system_prompt_crit_multipliers_match_code(self):
        """The prompt's crit sentence must state the code's roll counts (#105).

        It used to promise only "triple damage" on a confirmed crit and never
        mentioned the doubling on an unconfirmed one, so a model reading it
        under-delivered exactly the way ``resolve_attack`` used to. Both counts
        are rendered from the constants; this locks them.
        """
        from characters.combat import CRIT_DAMAGE_ROLLS, SEVERE_CRIT_DAMAGE_ROLLS
        from gm.engine import system_prompt

        self.assertIn(f"damage dice {CRIT_DAMAGE_ROLLS}×", system_prompt())
        self.assertIn(f"damage dice {SEVERE_CRIT_DAMAGE_ROLLS}×", system_prompt())
        # The severe crit is gated on the confirm roll, and it bleeds.
        self.assertIn("confirm", system_prompt())
        self.assertIn("bleeding", system_prompt())

    def test_cli_and_web_send_the_same_options_shape(self):
        """One ``options`` object for both clients (issue #324).

        The web GM builds it from settings, the CLI from the environment;
        both go through ``GMModelConfig.options`` so a new knob cannot be
        added to one side and forgotten on the other.
        """
        from cli.gm import GM_CONFIG
        from gm.services import get_gm_options

        with self.settings(
            GM_MODEL=GM_CONFIG.model,
            GM_NUM_CTX=GM_CONFIG.num_ctx,
            GM_TEMPERATURE=GM_CONFIG.temperature,
        ):
            self.assertEqual(get_gm_options(), GM_CONFIG.options())

    def test_engine_imports_without_django(self):
        """``gm.engine``'s import chain must stay Django-free (the CLI needs it).

        ``roll_dice`` now delegates to ``characters.dice``, which pulls
        ``characters.constants`` in with it, and ``characters.rules`` (read
        through the shared rules-file reader) is where ``characters.dice``
        takes its numbers from once issue #59 seats them, and
        ``characters.skill_rules`` (issue #70) is read the same way — a fresh
        interpreter proves none of them drags Django along. Run
        out-of-process because this test runner has Django loaded already.
        """
        script = (
            "import sys, gm.engine, characters.rules, characters.skill_rules\n"
            "loaded = sorted(m for m in sys.modules if m.split('.')[0] == 'django')\n"
            "assert not loaded, loaded\n"
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_importing_the_site_builds_no_prompt(self):
        """The prompts are built when a turn first asks, not at import (#70).

        Three sections are built from the loaded canon's
        ``skill-rules.json``, which nothing may read while modules import: a
        canon missing that file must fail ``manage.py check`` with
        ``characters.E002``, not crash the URL import. Run out of process:
        this runner has built them already.
        """
        from gm import engine

        builders = tuple(builder.__name__ for builder in engine.PROMPT_BUILDERS)
        script = (
            "import os, django\n"
            "os.environ.setdefault('DJANGO_SETTINGS_MODULE', "
            "'tarmar_studio.settings')\n"
            "django.setup()\n"
            "import tarmar_studio.urls\n"
            "from gm import engine\n"
            "for builder in engine.PROMPT_BUILDERS:\n"
            "    print(builder.__name__, builder.cache_info().misses)\n"
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            env={**os.environ, "DEBUG": "true"},
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            completed.stdout.split("\n")[:-1], [f"{name} 0" for name in builders]
        )

    def test_each_prompt_is_one_string_for_the_life_of_the_process(self):
        """Built once and then the same object, so the prefix cache holds (#590).

        A turn records its prompt under the text's hash (``GMPromptSnapshot``),
        so a prompt rebuilt to the same bytes stores nothing new either.
        """
        from gm import engine

        for builder in (
            engine.system_prompt,
            engine.aide_system_prompt,
            engine.dm_play_system_prompt,
            engine.dm_system_prompt,
        ):
            with self.subTest(builder=builder.__name__):
                self.assertIs(builder(), builder())
                self.assertIn(
                    builder(), engine.system_prompts_by_authority_and_mode().values()
                )
        for (
            is_game_master,
            mode,
        ), prompt in engine.system_prompts_by_authority_and_mode().items():
            with self.subTest(is_game_master=is_game_master, mode=mode):
                self.assertIs(engine.system_prompt_for(is_game_master, mode), prompt)

    def test_roll_dice_delegates_to_the_shared_parser(self):
        """One dice parser for the project: /roll goes through characters.dice."""
        from characters.dice import parse_dice_expression

        self.assertEqual(parse_dice_expression("2d6+3"), (2, 6, 3))
        with patch("gm.engine.roll_expression", return_value=([2, 3], 5)) as delegated:
            self.assertEqual(roll_dice("2d6"), ([2, 3], 5))
        delegated.assert_called_once_with("2d6")

    def test_system_prompt_rendered_text_snapshot(self):
        """Snapshot of the code-rendered attack-roll block (issue #104).

        The crit sentence, fumble bands and matrix are built from
        ``characters.combat`` at import time (``gm.engine._render_*``), so
        this locks the rendered text of exactly that block byte-for-byte —
        the part of the prompt that moves when the code moves. (It used to
        lock the whole prompt; since issue #324 the prompt is ~7k tokens of
        prose around this block, and its wording is covered by the section
        and canon tests below rather than a wall of literal text.) A failure
        here means either the wording changed on purpose (update the literal)
        or ``combat.MATRIX``/``FUMBLE_TABLE``/the crit constants changed and
        the rendered block moved with them (investigate before updating).
        """
        from gm.engine import system_prompt

        expected_attack_block = (
            "Natural 20 = auto-hit + critical: roll the weapon's damage dice 2× "
            "and sum. Then confirm — a second d20 at the same TN with the same "
            "bonus (natural 1 just fails to confirm, natural 20 auto-confirms); "
            "if it also hits, the blow is severe: damage dice 3× instead, plus "
            "bleeding, reaching Body as well as Fatigue. Natural 1 = auto-miss + "
            "fumble — roll 1d6: 1–3 off-balance (−2 to your next action); 4–5 "
            "drop weapon; 6 weapon takes stress (breaks on a second fumble).\n"
        )
        expected_matrix_line = (
            "Matrix — base TN by weapon class vs armour tier "
            "[None/Light/Medium/Heavy]: Piercing 11/14/18/22; Striking "
            "13/14/16/18; Thrusting 12/14/16/19; Heavy Striking 14/14/15/16; "
            "Heavy Thrusting 14/14/15/15; Missile — Bows 12/14/17/20; "
            "Missile — Crossbows 13/14/15/16; Flexible / Snare 13/16/19/22.\n"
        )
        self.assertIn(expected_attack_block, system_prompt())
        self.assertIn(expected_matrix_line, system_prompt())
        self.assertLess(
            system_prompt().index(expected_attack_block),
            system_prompt().index(expected_matrix_line),
        )

    def test_build_state_request_message(self):
        """State request message has user role."""
        msg = build_state_request_message()
        self.assertEqual(msg["role"], "user")
        self.assertIn("game state", msg["content"])

    def test_build_rules_lookup_message(self):
        """Rules lookup message contains query and rules text."""
        msg = build_rules_lookup_message("How does combat work?", "Rules here")
        self.assertEqual(msg["role"], "user")
        self.assertIn("How does combat work?", msg["content"])
        self.assertIn("Rules here", msg["content"])

    def test_build_roll_message(self):
        """Roll message contains expression and result."""
        msg = build_roll_message("3d6", [4, 5, 6], 15)
        self.assertEqual(msg["role"], "user")
        self.assertIn("3d6", msg["content"])
        self.assertIn("15", msg["content"])

    def test_load_rules_missing_dir(self):
        """load_rules returns None when directory doesn't exist."""
        with self.settings(RULES_DIR="/nonexistent/path"):
            result = load_rules()
            self.assertIsNone(result)


# ---------- View tests ----------


class GMViewTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.login_as(self.user)

    def test_chat_page_requires_login(self):
        """GM chat page requires authentication."""
        self.logout()
        response = self.client.get("/tarmar/gm/")
        self.assertEqual(response.status_code, 302)

    def test_chat_page_loads(self):
        """GM chat page loads for authenticated user."""
        response = self.client.get("/tarmar/gm/")
        self.assertEqual(response.status_code, 200)

    def _chat_config(self, response):
        """The parsed contents of the page's #chat-config json_script block."""
        import json as json_module
        import re as re_module

        content = response.content.decode()
        match = re_module.search(
            r'<script id="chat-config" type="application/json">(.*?)</script>',
            content,
            re_module.DOTALL,
        )
        self.assertIsNotNone(match, "the page has no #chat-config block")
        assert match is not None
        return json_module.loads(match.group(1))

    def test_chat_config_carries_every_value_chat_js_reads(self):
        """chat.js's template-supplied values arrive as one JSON block.

        They used to be an inline <script> declaring five globals, which put
        the last of this page's JavaScript out of Jest's reach (issue #63).
        Drop one of these keys and the corresponding feature — send, save,
        delete, the mode a first message opens a session in (issue #372), and
        the transcript download (issue #389) — breaks silently with an
        undefined value.
        """
        session = GMSession.objects.create(name="Test", user=self.user)
        response = self.client.get(f"/tarmar/gm/?session={session.pk}")
        config = self._chat_config(response)
        self.assertEqual(
            set(config),
            {
                "streamUrl",
                "saveUrl",
                "deleteUrl",
                "sessionId",
                "mode",
                "transcriptUrlTemplate",
                "reviewUrl",
                # Where a lost turn is asked after, and the turn still
                # running when the page loaded (issue #515).
                "turnUrlTemplate",
                "runningTurn",
            },
        )
        # Only a Game Master is given somewhere to send a review (issue #402).
        self.assertIsNone(config["reviewUrl"])
        # The Transcript button's download (issue #389), as a pattern the page
        # fills in with the session it is on (issue #436).
        self.assertEqual(
            config["transcriptUrlTemplate"],
            "/tarmar/gm/sessions/{session_id}/transcript/",
        )
        self.assertEqual(config["mode"], session.mode)
        self.assertEqual(config["streamUrl"], "/tarmar/gm/stream/")
        self.assertEqual(config["saveUrl"], "/tarmar/gm/save/")
        self.assertEqual(config["sessionId"], session.pk)
        # The CSRF token left this block in issue #338: chat.js asks
        # api.js for it, and base.html carries it in a meta tag.
        self.assertIn('<meta name="csrf-token" content="', response.content.decode())

    def test_chat_config_session_id_is_null_with_no_active_session(self):
        """chat.js starts an unsaved conversation with sessionId null."""
        response = self.client.get("/tarmar/gm/")
        self.assertIsNone(self._chat_config(response)["sessionId"])

    def test_chat_config_block_precedes_chat_js(self):
        """chat.js reads the block at parse time, so it must already be there."""
        response = self.client.get("/tarmar/gm/")
        content = response.content.decode()
        self.assertLess(
            content.index('id="chat-config"'),
            content.index("js/chat.js"),
            "#chat-config must be rendered before chat.js loads",
        )

    def test_chat_template_has_no_inline_script(self):
        """The page's JavaScript lives in files, not in the template.

        Issue #63's whole point: inline JavaScript is code Jest cannot reach.
        The json_script config block is `type="application/json"` — data, not
        code — and matched separately so it cannot mask a real one.
        """
        import re as re_module
        from pathlib import Path

        import gm

        source = (
            Path(gm.__file__).parent / "templates" / "gm" / "chat.html"
        ).read_text(encoding="utf-8")
        executable_blocks = [
            tag
            for tag in re_module.findall(r"<script[^>]*>", source)
            if "application/json" not in tag and "src=" not in tag
        ]
        self.assertEqual(executable_blocks, [])

    def test_chat_page_includes_dompurify_before_chat_js(self):
        """
        Assistant output is rendered via marked.parse() -> innerHTML (issue
        #141). DOMPurify must be loaded, and loaded before chat.js, or the
        DOMPurify.addHook() call at the top of chat.js silently no-ops and
        every render falls back to raw, unsanitized HTML.
        """
        response = self.client.get("/tarmar/gm/")
        self.assertContains(response, "js/dompurify.min.js")
        content = response.content.decode()
        dompurify_index = content.index("js/dompurify.min.js")
        chat_js_index = content.index("js/chat.js")
        self.assertLess(
            dompurify_index,
            chat_js_index,
            "dompurify.min.js must be <script>-included before chat.js",
        )

    def test_chat_page_includes_api_js_before_chat_js(self):
        """
        chat.js calls postWithCsrf/uiNotify/uiConfirm as bare globals that
        api.js publishes (issue #111). base.html loads api.js in <head>, ahead
        of the chat.js tag in this template's content block; reverse that and
        every send, save and delete throws ReferenceError.
        """
        response = self.client.get("/tarmar/gm/")
        self.assertContains(response, "js/api.js")
        content = response.content.decode()
        self.assertLess(
            content.index("js/api.js"),
            content.index("js/chat.js"),
            "api.js must be <script>-included before chat.js",
        )

    def test_chat_page_with_session(self):
        """Chat page loads with an active session."""
        session = GMSession.objects.create(name="Test", user=self.user)
        session.history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]
        session.save()
        response = self.client.get(f"/tarmar/gm/?session={session.pk}")
        self.assertEqual(response.status_code, 200)

    def test_chat_sessions_list_includes_delete_control(self):
        """Each sidebar session gets a delete button wired to gm_delete (issue #115).

        Previously chat.html shipped a delete endpoint but nothing in the page
        called it — the endpoint had no UI entry point. The endpoint now
        reaches chat.js through the #chat-config block (issue #63) rather than
        an inline `const DELETE_URL`, so that is what this checks for.
        """
        session = GMSession.objects.create(name="Test", user=self.user)
        response = self.client.get("/tarmar/gm/")
        # The date is the third argument since #705: two sessions named after
        # the same first message are one question otherwise.
        started = session.created_at.strftime("%b %d, %Y")
        self.assertContains(
            response, f"deleteSession({session.pk}, 'Test', '{started}')"
        )
        self.assertEqual(self._chat_config(response)["deleteUrl"], "/tarmar/gm/delete/")

    def test_chat_sessions_list_escapes_session_name_for_inline_js(self):
        """A session name with a quote must not break out of the onclick string."""
        session = GMSession.objects.create(name="Bob's Game", user=self.user)
        response = self.client.get("/tarmar/gm/")
        content = response.content.decode()
        self.assertNotIn(f"deleteSession({session.pk}, 'Bob's Game')", content)
        self.assertIn(f"deleteSession({session.pk}, 'Bob\\u0027s Game', ", content)

    def test_chat_page_invalid_session_404(self):
        """Chat page returns 404 for invalid session ID."""
        response = self.client.get("/tarmar/gm/?session=99999")
        self.assertEqual(response.status_code, 404)

    def test_chat_page_non_numeric_session_404(self):
        """Chat page returns 404 (not a 500) for a non-numeric session ID.

        Regression test for issue #148: a non-numeric ``session`` query param
        raised an uncaught ValueError from the pk lookup.
        """
        response = self.client.get("/tarmar/gm/?session=abc")
        self.assertEqual(response.status_code, 404)

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_stream_creates_session(self, mock_stream):
        """Streaming creates a new session when none exists."""
        mock_stream.side_effect = gm_replies(["Hello ", "world"])
        response = self.client.post(
            "/tarmar/gm/stream/",
            json.dumps({"message": "Hi"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        # Consume the stream
        content = b"".join(response.streaming_content)
        self.assertIn(b"Hello", content)
        # A new session should have been created
        self.assertEqual(GMSession.objects.count(), 1)

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_stream_appends_to_existing_session(self, mock_stream):
        """Streaming appends to existing session."""
        session = GMSession.objects.create(name="Existing", user=self.user)
        session.history = [{"role": "user", "content": "Old message"}]
        session.save()
        mock_stream.side_effect = gm_replies(["Response"])
        response = self.client.post(
            "/tarmar/gm/stream/",
            json.dumps({"session_id": session.pk, "message": "New"}),
            content_type="application/json",
        )
        b"".join(response.streaming_content)
        session.refresh_from_db()
        self.assertGreater(session.message_count, 1)

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_stream_merges_concurrent_session_update(self, mock_stream):
        """A concurrent send landing before this stream's save isn't lost."""
        session = GMSession.objects.create(name="Race", user=self.user)
        session.history = [{"role": "user", "content": "first"}]
        session.save()
        mock_stream.side_effect = gm_replies(["reply"])

        # The view loads the history snapshot now; the save runs lazily when the
        # stream is consumed below.
        response = self.client.post(
            "/tarmar/gm/stream/",
            json.dumps({"session_id": session.pk, "message": "second"}),
            content_type="application/json",
        )

        # Simulate a concurrent send committing before this stream's save.
        other = GMSession.objects.get(pk=session.pk)
        other.history = [
            {"role": "user", "content": "first"},
            {"role": "user", "content": "concurrent"},
            {"role": "assistant", "content": "concurrent-reply"},
        ]
        other.save()

        b"".join(response.streaming_content)  # triggers the merge-and-save

        session.refresh_from_db()
        contents = [m["content"] for m in session.history]
        # The concurrent turns survive (not overwritten) AND this request's
        # user + assistant turns are appended.
        self.assertIn("concurrent", contents)
        self.assertIn("concurrent-reply", contents)
        self.assertIn("second", contents)
        self.assertIn("reply", contents)

    def test_strip_thinking_removes_think_blocks(self):
        """strip_thinking drops <think>...</think>, incl. multiline / multiple."""
        from common.services.sse import strip_thinking

        self.assertEqual(strip_thinking("a<think>x</think>b"), "ab")
        self.assertEqual(strip_thinking("<think>\nmulti\nline\n</think>  hi"), "hi")
        self.assertEqual(
            strip_thinking("one<think>a</think>two<think>b</think>three"),
            "onetwothree",
        )
        self.assertEqual(strip_thinking("no think here"), "no think here")

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_stream_filters_thinking_tokens(self, mock_stream):
        """Reasoning between <think>/</think> is neither streamed nor persisted."""
        mock_stream.side_effect = gm_replies(
            ["Visible1 ", "<think>", "secret reasoning", "</think>", "Visible2"]
        )
        response = self.client.post(
            "/tarmar/gm/stream/",
            json.dumps({"message": "Hi"}),
            content_type="application/json",
        )
        content = b"".join(response.streaming_content)
        # Streamed tokens exclude the thinking content.
        self.assertIn(b"Visible1", content)
        self.assertIn(b"Visible2", content)
        self.assertNotIn(b"secret reasoning", content)
        # The persisted assistant message is stripped too.
        session = GMSession.objects.latest("id")
        assistant = [m for m in session.history if m["role"] == "assistant"][-1]
        self.assertNotIn("secret reasoning", assistant["content"])
        self.assertIn("Visible1", assistant["content"])

    def test_stream_rejects_overlong_message(self):
        """A message past the length cap is rejected with 400, not sent to Ollama."""
        response = self.client.post(
            "/tarmar/gm/stream/",
            json.dumps({"message": "x" * 5000}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_stream_rejects_malformed_json(self):
        """A non-JSON body is rejected with 400 rather than a 500."""
        response = self.client.post(
            "/tarmar/gm/stream/", "not json", content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)

    def test_stream_rejects_non_string_message(self):
        """A non-string message returns 400, not a 500.

        Regression test for issue #148: ``.strip()`` on a non-string
        ``message`` (e.g. a JSON number or object) raised an uncaught
        AttributeError.
        """
        response = self.client.post(
            "/tarmar/gm/stream/",
            json.dumps({"message": 123}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_stream_rejects_non_numeric_session_id(self):
        """A non-numeric session_id returns 400, not a 500.

        Regression test for issue #148: ``get_object_or_404`` only catches
        ``DoesNotExist``, so a non-numeric pk raised an uncaught ValueError
        from the database layer.
        """
        response = self.client.post(
            "/tarmar/gm/stream/",
            json.dumps({"session_id": "abc", "message": "Hi"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_stream_rejects_nonexistent_session_id(self):
        """A well-formed but nonexistent session_id is a clean JSON 404.

        Regression test for issue #165: ``get_object_or_404`` raising
        ``Http404`` for a nonexistent numeric id used to propagate uncaught,
        rendering Django's HTML 404 page to this JSON client.
        """
        response = self.client.post(
            "/tarmar/gm/stream/",
            json.dumps({"session_id": 999999, "message": "Hi"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertIn("error", response.json())

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_stream_reports_error_frame_when_streaming_fails(self, mock_stream):
        """A failure mid-stream yields an error frame instead of vanishing."""
        mock_stream.side_effect = RuntimeError("ollama exploded")
        with self.assertLogs("gm.views", level="ERROR"):
            response = self.client.post(
                "/tarmar/gm/stream/",
                json.dumps({"message": "Hi"}),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200)
            content = b"".join(response.streaming_content)
        self.assertIn(b"error", content)
        # The frame carries the fixed sentence, not the exception's text (#13).
        self.assertIn(json.dumps(views.GM_SERVER_ERROR_MESSAGE).encode(), content)
        self.assertNotIn(b"ollama exploded", content)

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    @patch("characters.dice.random.randint")
    def test_roll_command_rolls_once(self, mock_randint, mock_stream):
        """The roll shown to the client is the one the GM saw — rolled only once."""
        mock_randint.side_effect = [4, 5, 6, 7]  # distinct values per call
        mock_stream.side_effect = gm_replies(["ok"])
        response = self.client.post(
            "/tarmar/gm/stream/",
            json.dumps({"command": "roll", "message": "1d6"}),
            content_type="application/json",
        )
        content = b"".join(response.streaming_content).decode()
        self.assertIn('"total": 4', content)  # the first (and only) roll
        self.assertNotIn('"total": 5', content)  # not a second re-roll
        self.assertEqual(mock_randint.call_count, 1)

    def test_stream_requires_message(self):
        """Stream endpoint requires a message."""
        response = self.client.post(
            "/tarmar/gm/stream/",
            json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_stream_roll_invalid_expression(self):
        """Invalid roll expression returns error."""
        response = self.client.post(
            "/tarmar/gm/stream/",
            json.dumps({"command": "roll", "message": "invalid"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_save_session(self):
        """Save endpoint renames session."""
        session = GMSession.objects.create(name="Old Name", user=self.user)
        response = self.client.post(
            "/tarmar/gm/save/",
            json.dumps({"session_id": session.pk, "name": "New Name"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        session.refresh_from_db()
        self.assertEqual(session.name, "New Name")

    def test_save_no_session_id(self):
        """Save without session_id returns error."""
        response = self.client.post(
            "/tarmar/gm/save/",
            json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_save_rejects_malformed_json(self):
        """A non-JSON save body is rejected with 400 rather than a 500."""
        response = self.client.post(
            "/tarmar/gm/save/", "not json", content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)

    def test_save_rejects_non_string_name(self):
        """A non-string name returns 400, not a 500 (issue #148 sweep)."""
        session = GMSession.objects.create(name="Old Name", user=self.user)
        response = self.client.post(
            "/tarmar/gm/save/",
            json.dumps({"session_id": session.pk, "name": 123}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_save_rejects_non_numeric_session_id(self):
        """A non-numeric session_id returns 400, not a 500 (issue #148)."""
        response = self.client.post(
            "/tarmar/gm/save/",
            json.dumps({"session_id": "abc", "name": "New Name"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_save_rejects_nonexistent_session_id(self):
        """A well-formed but nonexistent session_id is a clean JSON 404 (issue #165)."""
        response = self.client.post(
            "/tarmar/gm/save/",
            json.dumps({"session_id": 999999, "name": "New Name"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertIn("error", response.json())

    def test_delete_session(self):
        """Delete endpoint removes session."""
        session = GMSession.objects.create(name="Doomed", user=self.user)
        response = self.client.post(
            "/tarmar/gm/delete/",
            json.dumps({"session_id": session.pk}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(GMSession.objects.count(), 0)

    def test_delete_no_session_id(self):
        """Delete without session_id returns error."""
        response = self.client.post(
            "/tarmar/gm/delete/",
            json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_delete_rejects_malformed_json(self):
        """A non-JSON delete body is rejected with 400 rather than a 500."""
        response = self.client.post(
            "/tarmar/gm/delete/", "not json", content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)

    def test_delete_rejects_non_numeric_session_id(self):
        """A non-numeric session_id returns 400, not a 500 (issue #148)."""
        response = self.client.post(
            "/tarmar/gm/delete/",
            json.dumps({"session_id": "abc"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_delete_rejects_nonexistent_session_id(self):
        """A well-formed but nonexistent session_id is a clean JSON 404 (issue #165)."""
        response = self.client.post(
            "/tarmar/gm/delete/",
            json.dumps({"session_id": 999999}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertIn("error", response.json())

    def test_sessions_list(self):
        """The chat page's session list shows only the requesting user's sessions."""
        GMSession.objects.create(name="Session 1", user=self.user)
        GMSession.objects.create(name="Session 2", user=self.user)
        # A session owned by someone else must not appear.
        GMSession.objects.create(name="Other", user=self.create_user(username="other1"))
        response = self.client.get("/tarmar/gm/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            sorted(session.name for session in response.context["sessions"]),
            ["Session 1", "Session 2"],
        )

    def test_unused_json_session_list_is_gone(self):
        """The capped JSON session list nothing called was removed (#339 item 12)."""
        response = self.client.get("/tarmar/gm/sessions/")
        self.assertEqual(response.status_code, 404)

    def test_cannot_access_another_users_session(self):
        """A user cannot read, save, or delete another user's GM session (IDOR)."""
        other = self.create_user(username="other2")
        session = GMSession.objects.create(name="Private", user=other)
        # chat page
        self.assertEqual(
            self.client.get(f"/tarmar/gm/?session={session.pk}").status_code, 404
        )
        # save
        self.assertEqual(
            self.client.post(
                "/tarmar/gm/save/",
                json.dumps({"session_id": session.pk, "name": "Hacked"}),
                content_type="application/json",
            ).status_code,
            404,
        )
        # delete
        self.assertEqual(
            self.client.post(
                "/tarmar/gm/delete/",
                json.dumps({"session_id": session.pk}),
                content_type="application/json",
            ).status_code,
            404,
        )
        self.assertTrue(GMSession.objects.filter(pk=session.pk).exists())

    def test_sessions_requires_login(self):
        """The chat page, which carries the session list, requires authentication."""
        self.logout()
        response = self.client.get("/tarmar/gm/")
        self.assertEqual(response.status_code, 302)

    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_stream_state_command(self, mock_stream):
        """State command streams response."""
        mock_stream.side_effect = gm_replies(["State info"])
        response = self.client.post(
            "/tarmar/gm/stream/",
            json.dumps({"command": "state"}),
            content_type="application/json",
        )
        content = b"".join(response.streaming_content)
        self.assertIn(b"State info", content)

    def test_stream_rules_no_dir(self):
        """Rules command returns error when rules dir missing."""
        with self.settings(RULES_DIR="/nonexistent"):
            response = self.client.post(
                "/tarmar/gm/stream/",
                json.dumps({"command": "rules", "message": "How does combat work?"}),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 400)


# ---------- Prompt content tests (issue #324) ----------


class GMSystemPromptContentTest(SimpleTestCase):
    """The full GM prompt now lives in ``gm.engine`` (issue #324).

    Until then the rich prompt sat in ``cli/Modelfile`` inside a custom
    ``tarmar-gm`` model — which neither client ever saw, because Ollama's
    ``/api/chat`` only prepends a Modelfile SYSTEM when the request's first
    message is not itself a ``system`` message, and both clients always send
    one. These tests pin the sections the prompt must carry, the canon names
    its world digest uses (``reference/content/`` wins over the old
    Modelfile), and a few things it must no longer say.
    """

    REQUIRED_HEADINGS = (
        "## Your Role",
        "## Design Philosophy",
        "## CRITICAL: Tarmar Is NOT D&D",
        "## Core Resolution Mechanic",
        "### Attributes",
        "### Derived Pools",
        "### Injury Thresholds",
        "## Character Creation",
        "### Experience Tracks",
        "### Levels and Skill Points",
        "## Combat System",
        "### Turn Sequence (6 phases)",
        "### Attack Rolls (d20 roll-over)",
        "### Situational Modifiers",
        "### Movement Speeds",
        "### Encumbrance",
        "### Forced Retreat",
        *MAGIC_HEADINGS,
        "## Equipment (Key Items)",
        "## GM Guidelines",
    )

    # The world section is the loaded canon's ``gm-prompt/world.md`` (issue
    # #919), so its headings are the canon's. Every canon's digest carries
    # these subsections, in this order; the Tarmar canon's own headings are
    # pinned in :meth:`test_the_tarmar_world_section_keeps_its_headings`.
    WORLD_SUBSECTIONS = (
        "### The Lands",
        "### The Powers",
        "### Peoples",
        "### GM-Only Background",
    )
    TARMAR_WORLD_HEADINGS = (
        "## The World of Tarmar",
        "### The Lands",
        "### The Navel",
        "### The Powers",
        "### Peoples",
        "### GM-Only Background",
    )

    # Names from the vault's legacy "Pillars" lore that the map superseded,
    # flavour glosses the Modelfile added without a source page, and the
    # prompt-level thinking hack the API's ``think`` field replaced.
    STALE_TEXT = (
        "Larmay",
        "Rotoring",
        "Gataland",
        "Angkor",
        "Sarif",
        "Norse",
        "Italian",
        "Demons",
        "/no_think",
        "tarmar-gm",
    )

    def test_every_section_is_present_in_order(self):
        from gm.engine import system_prompt

        positions = [
            system_prompt().index(heading) for heading in self.REQUIRED_HEADINGS
        ]
        self.assertEqual(positions, sorted(positions))

    def test_prompt_is_the_sections_joined(self):
        """``system_prompt()`` is exactly the section tuple, nothing hidden."""
        from gm.engine import system_prompt, system_prompt_sections

        self.assertEqual(system_prompt(), "\n\n".join(system_prompt_sections()))
        # Thirteen since issue #590 replaced the ledger, dice and procedures
        # sections with the records, outcomes and plan-format sections.
        self.assertEqual(len(system_prompt_sections()), 13)

    def test_the_dm_prompt_appends_and_changes_nothing_else(self):
        """The player's sections are untouched by the addendum.

        Two sections are appended for a Game Master: their authority over the
        numbers (#352) and the aide's manner (#353). Everything before them is
        the player's prompt, byte for byte. Counted against the player prompt
        rather than a literal, so adding a section to both (as #356 did with
        the ledger) does not read as the DM addendum having changed.
        """
        from gm.engine import (
            SECTION_DM_AIDE,
            SECTION_DM_AUTHORITY,
            dm_system_prompt,
            system_prompt,
        )

        self.assertEqual(
            dm_system_prompt(),
            "\n\n".join((system_prompt(), SECTION_DM_AUTHORITY, SECTION_DM_AIDE)),
        )

    def test_the_world_section_sits_between_equipment_and_guidelines(self):
        """The canon's world headings, in its order, where the world goes."""
        from gm.engine import SECTION_WORLD, system_prompt

        world_headings = [
            line for line in SECTION_WORLD.splitlines() if line.startswith("#")
        ]
        self.assertTrue(world_headings[0].startswith("## The World"))
        start = system_prompt().index("## Equipment (Key Items)")
        end = system_prompt().index("## GM Guidelines")
        positions = [
            system_prompt().index(heading, start) for heading in world_headings
        ]
        self.assertEqual(positions, sorted(positions))
        self.assertLess(positions[-1], end)
        for heading in self.WORLD_SUBSECTIONS:
            with self.subTest(heading=heading):
                self.assertTrue(
                    any(line.startswith(heading) for line in world_headings)
                )
        subsection_positions = [
            SECTION_WORLD.index(heading) for heading in self.WORLD_SUBSECTIONS
        ]
        self.assertEqual(subsection_positions, sorted(subsection_positions))

    @skipUnless(CANON_NAME == TARMAR_CANON_NAME, "pins the Tarmar canon's digest")
    def test_the_tarmar_world_section_keeps_its_headings(self):
        from gm.engine import SECTION_WORLD

        positions = [
            SECTION_WORLD.index(heading) for heading in self.TARMAR_WORLD_HEADINGS
        ]
        self.assertEqual(positions, sorted(positions))

    # The names the canon expects its digest to use come from the canon itself
    # (issue #921): several are on no public page.
    @skip_unless_canon_expects("world_section_names")
    def test_world_section_uses_canon_names(self):
        from gm.engine import SECTION_WORLD

        for name in CANON_EXPECTATIONS["world_section_names"]:
            with self.subTest(name=name):
                self.assertIn(name, SECTION_WORLD)

    def test_prompt_omits_stale_names_and_hacks(self):
        from gm.engine import system_prompt

        for text in self.STALE_TEXT:
            with self.subTest(text=text):
                self.assertNotIn(text, system_prompt())

    def test_mana_pool_follows_the_reference_rules(self):
        """``public-rules/magic.md``: the Pool starts at 0 and is not secret.

        Both the old quick reference and the Modelfile said "3d6 at creation
        (SECRET)", which the rules tree contradicts; canon wins.
        """
        from gm.engine import system_prompt

        self.assertIn("Mana Pool = the Spell Points earned", system_prompt())
        self.assertIn("starts at 0", system_prompt())
        self.assertNotIn("3d6 at creation", system_prompt())
        self.assertNotIn("SECRET", system_prompt())

    def test_gm_only_background_is_fenced(self):
        """The deep-history digest is marked for the GM alone."""
        from gm.engine import SECTION_WORLD

        self.assertIn("### GM-Only Background (never state to players", SECTION_WORLD)
        self.assertLess(
            SECTION_WORLD.index("### Peoples"),
            SECTION_WORLD.index("### GM-Only Background"),
        )

    def test_guidelines_frame_numbered_choices_as_non_exhaustive(self):
        """Issue #342: the chat page turns a trailing numbered list into
        buttons, so the prompt must ask for a shape that parser recognises
        (short one-line items, numbered 1..N, list last) and must keep the
        player free to type something else."""
        from gm.engine import SECTION_GUIDELINES

        self.assertIn("When offering numbered choices", SECTION_GUIDELINES)
        self.assertIn("number them 1..N", SECTION_GUIDELINES)
        self.assertIn("put the list last", SECTION_GUIDELINES)
        self.assertIn("describe any other action", SECTION_GUIDELINES)
        self.assertIn("choices are never exhaustive", SECTION_GUIDELINES)

    def test_prompt_size_stays_within_the_context_budget(self):
        """The longest of the four prompts stays under its share of GM_NUM_CTX.

        Measured in estimated tokens against ``SYSTEM_PROMPT_CONTEXT_SHARE``
        of the configured window, under whichever canon this run loaded
        (issue #907), so the ceiling moves with ``GM_NUM_CTX`` instead of by
        hand. The reasoning for the share is on the constant.

        History of the character cap this replaced: 36,000, then 37,500 for
        issues #375 and #376 (the offending sentence banned by name, each
        kept because a live-harness pass rate moved with it), 38,750 on
        2026-09-29 for #807, #802, #843 and #859 (the Game Master's records
        and the skill check named in the schema; the aging schedule, Origins
        and starting coin moved to ``rules_lookup``), and 40,000 on
        2026-09-30 for batch h (#876, #886). Content is not trimmed to fit a
        budget: batch h's two issue citations, first cut to make room, were
        restored. Growing the share is still a decision, not a default -- the
        next section that wants room should look for wording to replace first.
        """
        from django.conf import settings

        from gm.engine import system_prompt, system_prompts_by_authority_and_mode
        from gm.model_config import SYSTEM_PROMPT_CONTEXT_SHARE
        from gm.warmup import PromptBudget, estimate_longest_prompt_tokens

        self.assertGreaterEqual(len(system_prompt()), 20_000)
        budget = PromptBudget(
            prompt_tokens=estimate_longest_prompt_tokens(
                system_prompts_by_authority_and_mode().values()
            ),
            num_ctx=settings.GM_NUM_CTX,
        )
        self.assertTrue(
            budget.within_ceiling,
            f"the longest GM prompt under the {CANON_NAME!r} canon estimates at "
            f"{budget.prompt_tokens} tokens, over its ceiling of "
            f"{budget.ceiling_tokens} ({SYSTEM_PROMPT_CONTEXT_SHARE:.0%} of "
            f"GM_NUM_CTX {budget.num_ctx})",
        )

    def test_prompt_ceiling_leaves_most_of_the_pre_fold_history_to_play(self):
        """Prompt plus one turn's excerpts take at most half the fold line.

        A session folds its older turns once its history, system prompt
        included, passes ``FOLD_AT_FRACTION`` of the window less the reply
        reserve. Holding the prompt and an excerpt block under half of that
        is what keeps the conversation, not the prompt, the larger part of
        what a session carries (issue #907).
        """
        from django.conf import settings

        from gm.model_config import system_prompt_token_ceiling
        from gm.summary import FOLD_AT_FRACTION, usable_budget

        ceiling = system_prompt_token_ceiling(settings.GM_NUM_CTX)
        fold_line = FOLD_AT_FRACTION * usable_budget(settings.GM_NUM_CTX)
        self.assertLessEqual(
            ceiling + settings.GM_RETRIEVAL_MAX_TOKENS,
            fold_line / 2,
            f"a {ceiling}-token prompt ceiling plus "
            f"{settings.GM_RETRIEVAL_MAX_TOKENS} tokens of excerpts is more "
            f"than half the {fold_line:.0f}-token fold line at GM_NUM_CTX "
            f"{settings.GM_NUM_CTX}",
        )

    def test_ceiling_is_the_share_of_the_window_in_tokens(self):
        from gm.model_config import (
            SYSTEM_PROMPT_CONTEXT_SHARE,
            system_prompt_token_ceiling,
        )

        self.assertEqual(SYSTEM_PROMPT_CONTEXT_SHARE, 0.25)
        self.assertEqual(system_prompt_token_ceiling(65536), 16384)
        self.assertEqual(system_prompt_token_ceiling(32768), 8192)
        self.assertEqual(system_prompt_token_ceiling(10), 2)


# ---------- Model config / settings tests (issue #324) ----------


class GMModelConfigTest(SimpleTestCase):
    def test_defaults_when_nothing_is_set(self):
        from gm.model_config import (
            DEFAULT_GM_MODEL,
            DEFAULT_GM_NUM_CTX,
            DEFAULT_GM_TEMPERATURE,
            GMModelConfig,
        )

        config = GMModelConfig.from_environ({})
        self.assertEqual(config.model, DEFAULT_GM_MODEL)
        self.assertEqual(config.num_ctx, DEFAULT_GM_NUM_CTX)
        self.assertEqual(config.temperature, DEFAULT_GM_TEMPERATURE)
        self.assertEqual(DEFAULT_GM_MODEL, "qwen3:32b")
        self.assertEqual(DEFAULT_GM_NUM_CTX, 65536)
        self.assertEqual(DEFAULT_GM_TEMPERATURE, 0.8)

    def test_reads_all_three_variables(self):
        from gm.model_config import GMModelConfig

        config = GMModelConfig.from_environ(
            {"GM_MODEL": "llama3:8b", "GM_NUM_CTX": "8192", "GM_TEMPERATURE": "0.3"}
        )
        self.assertEqual(config, GMModelConfig("llama3:8b", 8192, 0.3))

    def test_blank_values_fall_back_to_defaults(self):
        """load_dotenv turns an uncommented empty line into ``""`` (issue #149)."""
        from gm.model_config import GMModelConfig

        config = GMModelConfig.from_environ(
            {"GM_MODEL": "  ", "GM_NUM_CTX": "", "GM_TEMPERATURE": ""}
        )
        self.assertEqual(config, GMModelConfig())

    def test_unparseable_number_names_the_variable(self):
        from gm.model_config import GMModelConfig

        with self.assertRaisesRegex(ValueError, "GM_NUM_CTX must be a int"):
            GMModelConfig.from_environ({"GM_NUM_CTX": "lots"})
        with self.assertRaisesRegex(ValueError, "GM_TEMPERATURE must be a float"):
            GMModelConfig.from_environ({"GM_TEMPERATURE": "warm"})

    def test_options_shape(self):
        from gm.model_config import GMModelConfig

        self.assertEqual(
            GMModelConfig("m", 4096, 0.5).options(),
            {"num_ctx": 4096, "temperature": 0.5},
        )

    def test_settings_expose_the_environment_config(self):
        """``settings.GM_*`` are what ``GMModelConfig.from_environ()`` reads."""
        from django.conf import settings

        from gm.model_config import GMModelConfig

        expected = GMModelConfig.from_environ()
        self.assertEqual(settings.GM_MODEL, expected.model)
        self.assertEqual(settings.GM_NUM_CTX, expected.num_ctx)
        self.assertEqual(settings.GM_TEMPERATURE, expected.temperature)

    def test_env_example_documents_the_variables(self):
        env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        for variable in ("GM_MODEL", "GM_NUM_CTX", "GM_TEMPERATURE"):
            with self.subTest(variable=variable):
                self.assertIn(f"# {variable}=", env_example)
        self.assertNotIn("TARMAR_GM_MODEL", env_example)


class GMRetrievalConfigTest(SimpleTestCase):
    """The retrieval knobs the web GM and the CLI both read (issue #337)."""

    def test_defaults_when_nothing_is_set(self):
        from gm.model_config import GMRetrievalConfig

        self.assertEqual(GMRetrievalConfig.from_environ({}), GMRetrievalConfig())

    def test_reads_every_variable(self):
        from gm.model_config import GMRetrievalConfig

        config = GMRetrievalConfig.from_environ(
            {
                "GM_RETRIEVAL_ENABLED": "false",
                "GM_EMBED_MODEL": "mxbai-embed-large",
                "GM_RETRIEVAL_TOP_K": "3",
                "GM_RETRIEVAL_MAX_TOKENS": "400",
                "GM_INDEX_PATH": "/tmp/index.json",
                "GM_CORPUS_ROOT": "/tmp/corpus",
            }
        )
        self.assertEqual(
            config,
            GMRetrievalConfig(
                enabled=False,
                embed_model="mxbai-embed-large",
                top_k=3,
                max_excerpt_tokens=400,
                index_path=Path("/tmp/index.json"),
                corpus_root=Path("/tmp/corpus"),
            ),
        )

    def test_enabled_needs_the_word_true(self):
        """Spelled the way ``settings.DEBUG`` reads its own flag."""
        from gm.model_config import GMRetrievalConfig

        self.assertTrue(GMRetrievalConfig.from_environ({}).enabled)
        self.assertTrue(
            GMRetrievalConfig.from_environ({"GM_RETRIEVAL_ENABLED": "TRUE"}).enabled
        )
        self.assertFalse(
            GMRetrievalConfig.from_environ({"GM_RETRIEVAL_ENABLED": "yes"}).enabled
        )

    def test_blank_values_fall_back_to_defaults(self):
        from gm.model_config import GMRetrievalConfig

        config = GMRetrievalConfig.from_environ(
            {
                "GM_RETRIEVAL_ENABLED": "",
                "GM_EMBED_MODEL": "  ",
                "GM_RETRIEVAL_TOP_K": "",
                "GM_INDEX_PATH": "",
            }
        )
        self.assertEqual(config, GMRetrievalConfig())

    def test_unparseable_number_names_the_variable(self):
        from gm.model_config import GMRetrievalConfig

        with self.assertRaisesRegex(ValueError, "GM_RETRIEVAL_TOP_K must be a int"):
            GMRetrievalConfig.from_environ({"GM_RETRIEVAL_TOP_K": "several"})

    def test_settings_expose_the_environment_config(self):
        """``settings.GM_RETRIEVAL_*`` are what the config reads.

        ``GM_RETRIEVAL_ENABLED`` is excluded: settings.py forces it off under
        pytest, which is a property of the run rather than the environment.
        """
        from django.conf import settings

        from gm.model_config import GMRetrievalConfig

        expected = GMRetrievalConfig.from_environ()
        self.assertEqual(settings.GM_EMBED_MODEL, expected.embed_model)
        self.assertEqual(settings.GM_RETRIEVAL_TOP_K, expected.top_k)
        self.assertEqual(settings.GM_RETRIEVAL_MAX_TOKENS, expected.max_excerpt_tokens)
        self.assertEqual(settings.GM_INDEX_PATH, expected.index_path)
        self.assertEqual(settings.GM_CORPUS_ROOT, expected.corpus_root)
        self.assertFalse(settings.GM_RETRIEVAL_ENABLED)


class GMServiceSettingsTest(TestCase):
    @override_settings(GM_MODEL="test-model")
    def test_get_gm_model_reads_settings(self):
        from gm.services import get_gm_model

        self.assertEqual(get_gm_model(), "test-model")

    @override_settings(GM_NUM_CTX=4096, GM_TEMPERATURE=0.1)
    def test_get_gm_options_reads_settings_per_call(self):
        from gm.services import get_gm_options

        self.assertEqual(get_gm_options(), {"num_ctx": 4096, "temperature": 0.1})


class GMStreamPayloadTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.login_as(self.create_user())

    @override_settings(GM_MODEL="test-model", GM_NUM_CTX=1024, GM_TEMPERATURE=0.5)
    @patch("gm.turn_engine.model.ollama_client.stream_chat")
    def test_stream_sends_settings_model_options_and_think_false(self, mock_stream):
        """The web GM hands Ollama the env-configured model, the options
        object, and ``think=False`` — the API-level replacement for the old
        ``/no_think`` prompt hack (issue #324)."""
        from gm.engine import system_prompt

        mock_stream.side_effect = gm_replies(["ok"])
        response = self.client.post(
            "/tarmar/gm/stream/",
            json.dumps({"message": "Hi"}),
            content_type="application/json",
        )
        b"".join(response.streaming_content)

        (model, history), kwargs = mock_stream.call_args
        self.assertEqual(model, "test-model")
        self.assertEqual(history[0], {"role": "system", "content": system_prompt()})
        # Bounded by the turn's deadline, and hung up on if the turn gives up
        # on it (issue #904).
        stop_at = kwargs.pop("stop_at")
        self.assertIsInstance(stop_at, float)
        self.assertIsInstance(kwargs.pop("hang_up"), ollama_client.HangUp)
        self.assertEqual(
            kwargs, {"options": {"num_ctx": 1024, "temperature": 0.5}, "think": False}
        )


class FakeMonotonicClock:
    """A stand-in for ``time.monotonic`` that moves only when told to.

    Patched in as the view's clock, it makes a stream-budget test measure the
    time the fake model says it took, however loaded the machine is.
    """

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        """Move the clock forward by ``seconds``."""
        self.now += seconds


class SlowFakeStream:
    """A token stream that stalls part-way through, standing in for the model.

    Nothing here touches Ollama. With a ``clock`` the stall moves that fake
    clock forward by ``stall_seconds`` and nothing else, so how long the turn
    took is decided by the test, not by how busy the machine is (issue #475:
    under a loaded parallel suite the real work before the first token alone
    outran a 0.05 s budget). Without one the stall is a real ``time.sleep``.
    ``emitted`` records what the view actually pulled and ``closed`` records
    whether the view shut the stream down, so a test can prove the connection
    is released rather than left dangling.
    """

    def __init__(
        self,
        before: list[str],
        stall_seconds: float,
        after: list[str],
        clock: "FakeMonotonicClock | None" = None,
    ) -> None:
        self.before = before
        self.stall_seconds = stall_seconds
        self.after = after
        self.clock = clock
        self.emitted: list[str] = []
        self.closed = False

    def __call__(self, *args: object, **kwargs: object) -> Iterator[str]:
        return self._tokens()

    def _tokens(self) -> Iterator[str]:
        try:
            for token in self.before:
                self.emitted.append(token)
                yield token
            if self.clock is not None:
                self.clock.advance(self.stall_seconds)
            else:
                time.sleep(self.stall_seconds)
            for token in self.after:
                self.emitted.append(token)
                yield token
        finally:
            self.closed = True


@override_settings(GM_STREAM_BUDGET_SECONDS=0.05)
class GMStreamBudgetTest(BaseTestCase):
    """A slow model must end the turn with a message, not a dead connection.

    Regression test for issue #361, where gunicorn's 30 s sync-worker timeout
    killed the worker mid-stream and the player saw only a connection error.
    The worker settings are the other half of that fix (see
    ``tarmar_studio.tests.test_service_unit``); this half is what the player
    reads when a turn really does run too long.
    """

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.login_as(self.user)
        self.clock = FakeMonotonicClock()
        self.slow_stream = SlowFakeStream(
            before=["The dust ", "settles"],
            stall_seconds=0.4,
            after=["a-late-token", "a-later-token"],
            clock=self.clock,
        )
        self.logged = ""

    def _post(self) -> bytes:
        # The view's clock is the fake one, so the budget runs out at the
        # stall and never before the first token (issue #475).
        view_time = Mock(wraps=time, monotonic=self.clock)
        with (
            patch("gm.turn_engine.model.ollama_client.stream_chat", self.slow_stream),
            patch("gm.views.time", view_time),
            patch("gm.turn_engine.engine.time", view_time),
        ):
            with self.assertLogs("gm.views", level="WARNING") as logs:
                response = self.client.post(
                    "/tarmar/gm/stream/",
                    json.dumps({"message": "What is behind the door?"}),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 200)
                content = b"".join(response.streaming_content)
        self.logged = "\n".join(logs.output)
        return content

    def test_slow_stream_ends_in_an_error_frame_the_player_can_read(self):
        content = self._post()
        frames = [
            json.loads(line[len(b"data: ") :])
            for line in content.split(b"\n\n")
            if line.startswith(b"data: ")
        ]
        # The stall comes while the turn is planning, so nothing was written.
        self.assertEqual(frames[0], {"status": "planning"})
        self.assertFalse([frame for frame in frames if "token" in frame])
        self.assertEqual(frames[-1], {"error": views.GM_STREAM_TOO_SLOW_MESSAGE})

    def test_slow_stream_sends_no_done_frame(self):
        content = self._post()
        self.assertNotIn(b'"done"', content)

    def test_slow_stream_abandons_the_model_and_closes_the_connection(self):
        """Nothing generated after the budget reaches the player, and the
        view stops pulling from the model instead of draining it politely."""
        content = self._post()
        self.assertNotIn(b"a-late-token", content)
        self.assertNotIn("a-later-token", self.slow_stream.emitted)
        self.assertTrue(self.slow_stream.closed)

    def test_slow_stream_persists_no_truncated_turn(self):
        self._post()
        self.assertEqual(GMSession.objects.count(), 0)

    def test_slow_stream_is_logged_as_a_budget_overrun_not_a_crash(self):
        self._post()
        self.assertIn("passed its 0.05s budget", self.logged)

    @override_settings(GM_STREAM_BUDGET_SECONDS=30)
    def test_a_stream_inside_the_budget_still_finishes_normally(self):
        """The budget only ends turns that overrun it.

        A real budget rather than the class's 0.05 s, so a loaded CI box
        cannot make a stream that never stalls look like an overrun.
        """
        with patch(
            "gm.turn_engine.model.ollama_client.stream_chat",
            SlowFakeStream(before=["All "], stall_seconds=0.0, after=["well."]),
        ):
            response = self.client.post(
                "/tarmar/gm/stream/",
                json.dumps({"message": "Hi"}),
                content_type="application/json",
            )
            content = b"".join(response.streaming_content)
        self.assertIn(b'"done"', content)
        self.assertIn(b"All well.", content)
        self.assertEqual(GMSession.objects.count(), 1)


# ---------- The prompt against the rules pages (issue #807) ----------

PUBLIC_RULES = REPO_ROOT / "reference" / "content" / "public-rules"


def _rules_page(relative: str) -> str:
    return (PUBLIC_RULES / relative).read_text()


class PromptMatchesTheRulesPagesTest(SimpleTestCase):
    """Each sentence #807 found wrong or missing, held to the page it states.

    Every test quotes the page it checks, so a page that changes breaks the
    test that relies on it, and the fragments are built from the tables the
    code uses, so a table that changes changes the prompt with it.
    """

    def setUp(self):
        from gm.engine import system_prompt

        self.prompt = system_prompt()

    def test_the_forced_retreat_phase_is_the_pages_row_six(self):
        # Issue #876: the turn sequence still armed a push with any damage
        # after tarmar-engine 0.9.5 (#813) armed it with physical hits past
        # the armour only, and turn-sequence.md row 6 said so. The prompt
        # names the physical hits in fewer words than the row: the row's
        # full wording measured worse on the scene_objects board (batch h).
        from gm import engine

        page = _rules_page("combat/turn-sequence.md")
        row = next(
            [cell.strip() for cell in line.strip().strip("|").split("|")]
            for line in page.splitlines()
            if line.startswith("| 6 ")
        )
        self.assertEqual(row[1], "**Forced Retreat**")
        self.assertTrue(row[3].startswith("Those who dealt physical hits"), row[3])
        self.assertIn("took none", row[3])
        checked = []
        for name in (
            "system_prompt",
            "aide_system_prompt",
            "dm_system_prompt",
            "dm_play_system_prompt",
        ):
            prompt = getattr(engine, name)()
            if "### Turn Sequence" not in prompt:
                continue
            checked.append(name)
            with self.subTest(prompt=name):
                self.assertIn(
                    "6. Forced Retreat: those who dealt physical hits and took "
                    "none push enemies back",
                    prompt,
                )
                self.assertNotIn("dealt damage and took none", prompt)
        # Every prompt carries the turn sequence; a check that skipped them
        # all would pass on nothing.
        self.assertEqual(len(checked), 4, checked)

    def test_bare_handed_damage_has_every_row_of_the_page(self):
        page = _rules_page("combat/action-options/special-combat-situations.md")
        # "| 11–12     | 1d6−2  | 25–30 | 1d6+3  |" and the two rows below it.
        for low, high, damage in (
            (25, 30, "1d6+3"),
            (31, 40, "2d6+1"),
            (41, 50, "3d6+1"),
        ):
            with self.subTest(strength=f"{low}-{high}"):
                self.assertIn(f"| {low}–{high} | {damage}", page)
                self.assertIn(f"{low}-{high} {damage}", self.prompt)

    def test_armours_dex_penalty_is_aim_and_dodge_only(self):
        page = _rules_page("characters/derived-pools.md")
        self.assertIn(
            "Effective DEX here does *not* include armor's DEX penalty (unlike "
            "the DEX used for aim/dodge).",
            page,
        )
        self.assertNotIn("applies to everything DEX governs", self.prompt)
        self.assertIn("Armour's DEX penalty applies to aim and dodge only", self.prompt)

    def test_tarmar_has_checks_and_saves_of_its_own(self):
        page = _rules_page("characters/injury-thresholds-death.md")
        self.assertIn("they begin making survival saves (3d6 ≤ CON)", page)
        self.assertNotIn("saving throws, or ability checks", self.prompt)
        self.assertIn("D&D saving throws and ability checks", self.prompt)
        self.assertIn("survival save each turn (3d6 ≤ CON)", self.prompt)

    def test_the_aging_schedule_is_the_pages(self):
        from gm import rules_lookup

        said_by_lookup = rules_lookup.look_up("aging")
        page = _rules_page("characters/prior-experience/aging.md")
        # "| Age | 32  | 36  | 40  | ..." with a row per attribute.
        rows = {
            cells[0]: cells[1:]
            for line in page.splitlines()
            if line.startswith("| ") and not line.startswith("| ---")
            for cells in [[cell.strip() for cell in line.strip("|").split("|")]]
        }
        ages = rows.pop("Age")
        for age_index, age in enumerate(ages):
            said = [
                f"{attribute} {row[age_index]}"
                for attribute, row in rows.items()
                if row[age_index] != "0"
            ]
            earlier = [
                f"{attribute} {row[age_index - 1]}"
                for attribute, row in rows.items()
                if age_index and row[age_index - 1] != "0"
            ]
            if said and said != earlier:
                with self.subTest(age=age):
                    self.assertIn(f"{age} {', '.join(said)}", said_by_lookup)
        self.assertIn("aging", self.prompt, "offered as a lookup subject")

    def test_each_origin_gives_what_the_page_says(self):
        page = _rules_page("characters/location.md")
        self.assertIn(
            "City literacy is 3d6 ≤ INT; Village literacy is 3d6 ≤ INT − 2; "
            "Rural literacy is 3d6 ≤ INT − 4.",
            page,
        )
        self.assertIn(
            "**Village:** Street smarts OR survival (choose); Literacy on INT check +2",
            page,
        )
        from gm import rules_lookup

        origins = rules_lookup.look_up("origins")
        for said in (
            "City: Street Smarts, literacy check 3d6 ≤ INT;",
            "Village: Street Smarts or Survival, literacy check 3d6 ≤ INT − 2;",
            "Rural: Survival, literacy check 3d6 ≤ INT − 4;",
        ):
            with self.subTest(origin=said.split(":")[0]):
                self.assertIn(said, origins)

    def test_reading_is_the_check_and_the_skill_is_how_well(self):
        page = _rules_page("characters/location.md")
        self.assertIn(
            "This is the check to be literate at all, and is separate from the "
            "[Literacy skill]",
            page,
        )
        from gm import rules_lookup

        self.assertIn(
            "That check decides whether a character can read; the Literacy "
            "skill is only how well.",
            self.prompt,
        )
        self.assertIn(
            "The literacy check decides whether a character can read at all; "
            "the Literacy skill is only how well.",
            rules_lookup.look_up("origins"),
        )

    def test_the_starting_coin_is_the_pages(self):
        from gm import rules_lookup

        coin = rules_lookup.look_up("starting_coin")
        page = _rules_page("characters/wealth.md")
        for row, said in (
            ("| 0-15       | Subsistence | 10                    |", "Subsistence 10"),
            ("| 16-70      | Moderate    | 100                   |", "Moderate 100"),
            (
                "| 71-95      | Merchant    | 100 + percentile dice |",
                "Merchant 100 + percentile dice",
            ),
            (
                "| 96-100     | Rich        | Consult GM            |",
                "Rich: the Game Master sets it",
            ),
        ):
            with self.subTest(level=said.split()[0]):
                self.assertIn(row, page)
                self.assertIn(said, coin)

    def test_the_modifier_table_is_the_pages_and_the_codes(self):
        import re

        from characters.skills import calculate_attribute_modifier

        page = _rules_page("characters/attributes.md")
        table = page.split("| Attribute | Modifier |")[1].split("\n\n")[0]
        # "| 3         | −5       |" ... "| 8-13      | 0        |" ...
        rows = [
            [cell.strip() for cell in line.strip("|").split("|")]
            for line in table.strip().splitlines()[1:]
        ]
        self.assertEqual(len(rows), 11)
        for label, modifier in rows:
            with self.subTest(score=label):
                self.assertRegex(
                    self.prompt,
                    rf"(?:Modifiers: |, ){re.escape(label)}(?: or less| or more)?="
                    rf"{re.escape(modifier)}[,.]",
                )
        # attributes.md (sheet rules, #893): "a score below 3 takes −5 and a
        # score above 18 takes +5, the modifier of the nearest row".
        flat_past_the_ends = calculate_attribute_modifier(
            19
        ) == calculate_attribute_modifier(18)
        if flat_past_the_ends:
            self.assertIn(
                "**a score below 3 takes −5 and a score above 18\ntakes +5**", page
            )
        self.assertEqual(
            "Below 3 the modifier stays −5 and above 18 it stays +5." in self.prompt,
            flat_past_the_ends,
        )
        self.assertEqual(
            "keeps changing by 1 a point" in self.prompt, not flat_past_the_ends
        )

    def test_play_reads_the_aged_score(self):
        page = _rules_page("characters/derived-pools.md")
        self.assertIn('"Effective" means after aging penalties are applied', page)
        self.assertIn(
            "Play uses aged scores everywhere; only the literacy check and the "
            "weight from STR use the base score.",
            self.prompt,
        )

    def test_the_fatigue_roll_is_made_with_the_character(self):
        """#859: the chat rolls it at creation, as the creation page does."""
        self.assertIn(
            "2. The 2d6 Fatigue roll is made as the character is made", self.prompt
        )
        self.assertIn("a new character's is rolled as it is made", self.prompt)
