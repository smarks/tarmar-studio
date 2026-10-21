"""The GM pages' text limits are the server's, and past them is a refusal (#938).

The Campaign tab, a hand-set person or thing, the Luck control and the recap
correction typed their ``maxlength`` out as literal numbers, and two foe-name
paths cut a name to a literal 100. The pages now read ``gm_text_limits``
(:data:`gm.text_limits.GM_TEXT_LIMITS`, the model columns' own lengths), and
every path that stores one of these texts refuses one character more with
the usual wording rather than cutting it short.
"""

import json
from html.parser import HTMLParser
from unittest import mock

from django.template.loader import render_to_string
from django.urls import reverse

from battle.models import Battle
from bestiary.models import Beast
from characters.services import CharacterChangeError
from common.test_helpers import (
    BaseTestCase,
    create_character,
    limit_refusal_parts,
    too_long_refusal,
)

from .fight import FOE_SCHEMA, FightError, FoeRequest, stand_in_beast
from .foe_people import FOE_NAME_MAX_LENGTH, person_foe
from .hand_records import add_ruling, open_thread, save_entity
from .luck import MAX_ROLL_NOTE, LuckAccount, LuckPanel, spend_luck
from .models import (
    GMRecordEdit,
    GMSession,
    SceneEntity,
    SessionCharacter,
    StoryThread,
    TableRuling,
)
from .summary import MAX_SUMMARY_CHARS
from .templatetags.gm_play import luck_control
from .text_limits import GM_TEXT_LIMITS

#: Limits no template would carry by chance, so a page that shows them read
#: them from the map rather than from a number typed into it.
ODD_LIMITS = {
    "ruling_topic": 61,
    "ruling_text": 401,
    "thread_text": 301,
    "thread_resolution": 302,
    "entity_name": 81,
    "entity_where": 201,
    "entity_wants": 202,
    "entity_condition": 203,
    "entity_notes": 204,
    "luck_roll": 121,
    "fold_recap": 2001,
}

#: Each Campaign-tab input, as (the endpoint its form posts to, its name),
#: to the limit it is held to.
CAMPAIGN_INPUTS = {
    ("rulings", "topic"): "ruling_topic",
    ("rulings", "text"): "ruling_text",
    ("threads", "text"): "thread_text",
    ("threads", "resolution"): "thread_resolution",
    ("entities", "name"): "entity_name",
    ("entities", "where"): "entity_where",
    ("entities", "wants"): "entity_wants",
    ("entities", "condition"): "entity_condition",
    ("entities", "notes"): "entity_notes",
}


class _MaxLengths(HTMLParser):
    """Every text input's ``maxlength``, keyed by its form's endpoint and name.

    The endpoint is the last part of the form's ``data-url`` ("rulings"), or
    "" outside a form; an input with no name is keyed by its id or class.
    """

    def __init__(self) -> None:
        super().__init__()
        self.endpoint = ""
        self.found: dict[tuple[str, str], set[str]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: value or "" for key, value in attrs}
        if tag == "form":
            self.endpoint = attributes.get("data-url", "").rstrip("/").split("/")[-1]
        elif tag in ("input", "textarea") and "maxlength" in attributes:
            key = (
                attributes.get("name")
                or attributes.get("id")
                or attributes.get("class", "")
            )
            self.found.setdefault((self.endpoint, key), set()).add(
                attributes["maxlength"]
            )

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self.endpoint = ""


def maxlengths(html: str) -> dict[tuple[str, str], set[str]]:
    """Every text input's ``maxlength`` in ``html``; see :class:`_MaxLengths`."""
    parser = _MaxLengths()
    parser.feed(html)
    return parser.found


class LimitsAreTheColumnsTest(BaseTestCase):
    def test_each_limit_is_its_columns_length(self):
        def column(model, field: str) -> int:
            return model._meta.get_field(field).max_length

        self.assertEqual(
            GM_TEXT_LIMITS,
            {
                "ruling_topic": column(TableRuling, "topic"),
                "ruling_text": column(TableRuling, "text"),
                "thread_text": column(StoryThread, "text"),
                "thread_resolution": column(StoryThread, "resolution"),
                "entity_name": column(SceneEntity, "name"),
                "entity_where": column(SceneEntity, "where"),
                "entity_wants": column(SceneEntity, "wants"),
                "entity_condition": column(SceneEntity, "condition"),
                "entity_notes": column(SceneEntity, "notes"),
                "luck_roll": MAX_ROLL_NOTE,
                "fold_recap": MAX_SUMMARY_CHARS,
            },
        )
        self.assertEqual(FOE_NAME_MAX_LENGTH, column(Beast, "name"))


class CampaignTabLimitsTest(BaseTestCase):
    """The Campaign tab's forms, adding and correcting, stop at the map's."""

    def setUp(self):
        super().setUp()
        self.game_master = self.create_dm(username="limits-gm")
        self.session = GMSession.objects.create(user=self.game_master, name="Crypt")
        add_ruling(self.session, self.game_master, "crits", "a 20 crits")
        open_thread(self.session, self.game_master, "who took the box")
        for kind in (SceneEntity.PERSON, SceneEntity.THING):
            save_entity(self.session, self.game_master, kind, {"name": f"a {kind}"})
        self.login_as(self.game_master)

    def rendered(self) -> dict[tuple[str, str], set[str]]:
        page = self.client.get(reverse("gm:campaign_panel", args=[self.session.pk]))
        self.assertEqual(page.status_code, 200)
        return maxlengths(page.content.decode())

    def test_every_input_carries_its_columns_length(self):
        found = self.rendered()
        for key, field in CAMPAIGN_INPUTS.items():
            with self.subTest(input=key):
                self.assertEqual(found.get(key), {str(GM_TEXT_LIMITS[field])})

    def test_the_tab_reads_the_map(self):
        with mock.patch.dict(GM_TEXT_LIMITS, ODD_LIMITS):
            found = self.rendered()
        for key, field in CAMPAIGN_INPUTS.items():
            with self.subTest(input=key):
                self.assertEqual(found.get(key), {str(ODD_LIMITS[field])})


class LuckAndRecapLimitsTest(BaseTestCase):
    def test_the_luck_roll_input_reads_the_map(self):
        panel = LuckPanel(url="/spend/", accounts={5: LuckAccount(2, 0)})
        for limits in (GM_TEXT_LIMITS, ODD_LIMITS):
            with self.subTest(limits=limits["luck_roll"]):
                with mock.patch.dict(GM_TEXT_LIMITS, limits):
                    html = render_to_string(
                        "gm/_luck_control.html", luck_control(panel, 5)
                    )
                self.assertEqual(
                    maxlengths(html)[("", "gm-luck-roll-5")],
                    {str(limits["luck_roll"])},
                )

    def test_the_recap_correction_reads_the_map(self):
        game_master = self.create_dm(username="recap-limit-gm")
        history = [{"role": "user", "content": f"message {turn}"} for turn in range(6)]
        session = GMSession.objects.create(
            user=game_master,
            name="Long",
            history_json=json.dumps(history),
            summary="The crypt is quiet.",
            summary_covers=4,
        )
        self.login_as(game_master)
        for limits in (GM_TEXT_LIMITS, ODD_LIMITS):
            with self.subTest(limits=limits["fold_recap"]):
                with mock.patch.dict(GM_TEXT_LIMITS, limits):
                    page = self.client.get(f"/tarmar/gm/?session={session.pk}")
                found = maxlengths(page.content.decode())
                self.assertEqual(found[("recap", "text")], {str(limits["fold_recap"])})


class HandEditRefusalsTest(BaseTestCase):
    """One character past each limit is refused, in the usual words."""

    def setUp(self):
        super().setUp()
        self.game_master = self.create_dm(username="refusal-gm")
        self.session = GMSession.objects.create(
            user=self.game_master, name="Crypt", summary="Quiet.", summary_covers=2
        )
        self.thread = open_thread(self.session, self.game_master, "who took it")
        self.login_as(self.game_master)

    def post(self, endpoint: str, body: dict):
        return self.client.post(
            reverse(endpoint, args=[self.session.pk]),
            json.dumps(body),
            content_type="application/json",
        )

    def bodies(self, field: str, text: str) -> tuple[str, dict]:
        """The endpoint and body that store ``text`` in ``field``."""
        thread_id = self.thread.edit.record_id
        return {
            "ruling_topic": (
                "gm:rulings",
                {"change": "add", "topic": text, "text": "r"},
            ),
            "ruling_text": (
                "gm:rulings",
                {"change": "add", "topic": "t", "text": text},
            ),
            "thread_text": ("gm:threads", {"change": "open", "text": text}),
            "thread_resolution": (
                "gm:threads",
                {"change": "resolve", "thread_id": thread_id, "resolution": text},
            ),
            "entity_name": (
                "gm:entities",
                {"change": "save", "kind": "person", "name": text},
            ),
            **{
                f"entity_{detail}": (
                    "gm:entities",
                    {"change": "save", "kind": "person", "name": detail, detail: text},
                )
                for detail in ("where", "wants", "condition", "notes")
            },
            "fold_recap": ("gm:recap", {"change": "edit", "text": text}),
        }[field]

    def test_one_past_each_limit_is_refused_and_nothing_is_written(self):
        for field, limit in GM_TEXT_LIMITS.items():
            if field == "luck_roll":
                continue  # LuckRollLimitTest
            with self.subTest(field=field):
                edits = GMRecordEdit.objects.count()
                endpoint, body = self.bodies(field, "x" * (limit + 1))
                response = self.post(endpoint, body)
                self.assertEqual(response.status_code, 400)
                for part in [*limit_refusal_parts(), str(limit)]:
                    self.assertIn(part, response.json()["error"])
                self.assertEqual(GMRecordEdit.objects.count(), edits)

    def test_a_text_at_each_limit_is_kept_whole(self):
        for field, limit in GM_TEXT_LIMITS.items():
            if field in ("luck_roll", "thread_resolution"):
                continue  # Luck below; a thread resolves once
            with self.subTest(field=field):
                endpoint, body = self.bodies(field, "y" * limit)
                response = self.post(endpoint, body)
                self.assertEqual(response.status_code, 200, response.content)
        self.session.refresh_from_db()
        self.assertEqual(self.session.summary, "y" * MAX_SUMMARY_CHARS)


class LuckRollLimitTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.player = self.create_user(username="luck-limit")
        self.character = create_character(self.player, name="Wren", skills={"luck": 3})
        self.session = GMSession.objects.create(user=self.player, name="The Barrow")
        self.session.characters.add(self.character)

    def test_a_roll_note_past_the_limit_is_refused_and_nothing_is_spent(self):
        with self.assertRaisesMessage(
            CharacterChangeError, f"The roll must be {MAX_ROLL_NOTE} characters"
        ):
            spend_luck(
                self.session,
                self.character.pk,
                is_game_master=False,
                roll="r" * (MAX_ROLL_NOTE + 1),
            )
        self.assertFalse(
            SessionCharacter.objects.filter(
                session=self.session, luck_spent__gt=0
            ).exists()
        )

    def test_the_endpoint_says_so(self):
        self.login_as(self.player)
        response = self.client.post(
            reverse("gm:spend_luck", args=[self.session.pk]),
            json.dumps(
                {"character_id": self.character.pk, "roll": "r" * (MAX_ROLL_NOTE + 1)}
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        for part in [*limit_refusal_parts(), str(MAX_ROLL_NOTE)]:
            self.assertIn(part, response.json()["error"])

    def test_a_roll_note_at_the_limit_is_kept_whole(self):
        roll = "r" * MAX_ROLL_NOTE
        line = spend_luck(
            self.session, self.character.pk, is_game_master=False, roll=roll
        )
        self.assertIn(f"to {roll};", line)


class FoeNameLimitTest(BaseTestCase):
    def test_a_stand_in_name_past_the_column_is_refused_not_cut(self):
        beasts = Beast.objects.count()
        with self.assertRaisesMessage(
            FightError,
            too_long_refusal("A foe's name", FOE_NAME_MAX_LENGTH),
        ):
            stand_in_beast(FoeRequest("q" * (FOE_NAME_MAX_LENGTH + 1), 1))
        self.assertEqual(Beast.objects.count(), beasts)

    def test_a_stand_in_name_at_the_column_is_kept_whole(self):
        name = "q" * FOE_NAME_MAX_LENGTH
        variant, _base = stand_in_beast(FoeRequest(name, 1))
        variant.refresh_from_db()
        self.assertEqual(variant.name, name)

    def test_a_persons_board_name_is_held_to_a_beasts_length(self):
        asked_for = "scarred " * 20 + "bandit"
        person = person_foe(asked_for)
        assert person is not None
        self.assertGreater(len(asked_for), FOE_NAME_MAX_LENGTH)
        self.assertEqual(len(person.label), FOE_NAME_MAX_LENGTH)
        self.assertTrue(person.label.startswith("Scarred scarred"))


class FoeNameLimitThroughTheFightTest(BaseTestCase):
    """The limit as the model is told it and as ``/fight`` holds it (#938)."""

    def test_the_model_is_told_the_limit(self):
        name = FOE_SCHEMA["properties"]["foes"]["items"]["properties"]["name"]
        self.assertEqual(name["maxLength"], FOE_NAME_MAX_LENGTH)

    def test_a_later_name_past_the_limit_writes_nothing(self):
        game_master = self.create_dm(username="fight-limit-gm")
        self.login_as(game_master)
        session = GMSession.objects.create(name="Quest", user=game_master)
        session.characters.add(create_character(game_master, name="Aldwin"))
        beasts = Beast.objects.count()
        too_long_name = "q" * (FOE_NAME_MAX_LENGTH + 1)

        # A catalog beast, a stand-in that would be built, then the refusal.
        response = self.client.post(
            reverse("gm:fight"),
            json.dumps(
                {
                    "session_id": session.pk,
                    "foes": f"2 gray wolves, a shadow hound, {too_long_name}",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["error"],
            too_long_refusal("A foe's name", FOE_NAME_MAX_LENGTH),
        )
        session.refresh_from_db()
        self.assertIsNone(session.battle)
        self.assertFalse(Battle.objects.exists())
        self.assertEqual(Beast.objects.count(), beasts)
