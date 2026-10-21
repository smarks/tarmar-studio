"""Who sits in a seat is stated once (issue #86).

Whether an account or a guest holds a seat is ``battle.access.seat_is_held``,
with ``claimed_seat_q`` beside it as the query form, and what to call whoever
holds it is ``battle.control.seat_holder_label``, which asks ``seat_is_held``
first. The lobby's seat rows and the pending choice's payload used to restate
the label rule inline; they read it from ``seat_holder_label`` now, and a scan
keeps any other module from writing the held test out again.
"""

import ast
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from battle.access import Actor
from battle.models import Battle
from battle.services import create_battle
from battle.views import pending_payload, seat_summaries
from battle.wording import words
from common.test_helpers import BaseTestCase

#: The seat fields whose being set means somebody holds it.
HOLDER_FIELDS = frozenset({"guest_key", "player_id", "player"})

#: The functions that may ask those fields directly, by module: the held test
#: itself and its query form, and the two that select the seats one guest
#: holds and no account does (an actor's own seats, and adopting a guest's
#: rows into the account it signs in as).
STATEMENTS_OF_THE_RULE = {
    "battle/access.py": frozenset({"seat_is_held", "claimed_seat_q", "seat_filter"}),
    "battle/adoption.py": frozenset({"adopt_guest_rows"}),
}

#: The calls whose keywords are a query's conditions.
QUERY_CALLS = frozenset({"filter", "exclude", "get", "Q"})

#: By the code's naming convention, receivers whose ``guest_key`` or ``player``
#: is not a seat's: the asker (``self``, ``actor``, ``viewer``) and the battle
#: or draft, whose ``guest_key`` is its creator's.
NOT_SEATS = frozenset({"self", "actor", "viewer", "battle", "draft"})

#: The values a holder field holds when nobody is there.
EMPTY = (None, "")


def _holder_field(node: ast.expr) -> bool:
    """Whether ``node`` reads a seat's holder field (``seat.guest_key`` or
    ``getattr(seat, "guest_key")``)."""
    if isinstance(node, ast.Attribute):
        receiver = node.value
        return node.attr in HOLDER_FIELDS and not (
            isinstance(receiver, ast.Name) and receiver.id in NOT_SEATS
        )
    if (
        isinstance(node, ast.Call)
        and getattr(node.func, "id", "") == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value in HOLDER_FIELDS
    ):
        return not (isinstance(node.args[0], ast.Name) and node.args[0].id in NOT_SEATS)
    return False


def _compares_to_empty(node: ast.expr) -> bool:
    """``field is None``, ``field != ""`` and their kin, or ``bool(field)``."""
    if (
        isinstance(node, ast.Compare)
        and len(node.ops) == 1
        and isinstance(node.ops[0], (ast.Is, ast.IsNot, ast.Eq, ast.NotEq))
        and isinstance(node.comparators[0], ast.Constant)
        and node.comparators[0].value in EMPTY
    ):
        return _holder_field(node.left)
    if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "bool":
        return bool(node.args) and _holder_field(node.args[0])
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return _holder_field(node.operand) or _compares_to_empty(node.operand)
    return False


def _is_empty(node: ast.expr) -> bool:
    """Whether ``node`` is ``None`` or ``""``."""
    return isinstance(node, ast.Constant) and node.value in EMPTY


def _held_condition(path: str, value: ast.expr) -> bool:
    """Whether one query condition, ``path=value``, asks if anybody holds a
    seat.

    Covered spellings, on the seat itself or across a relation to it:
    ``player__isnull`` and ``player_id__isnull`` (any value); ``player`` and
    ``player_id`` equal to ``None``, bare or with ``__exact``;
    ``guest_key__isnull`` (any value); ``guest_key`` equal to ``""``, bare or
    with ``__exact`` or ``__iexact``; and ``guest_key__in`` a literal list,
    tuple or set holding ``""``. A path through a receiver in
    :data:`NOT_SEATS` (``battle__guest_key``) is not a seat's and is skipped.
    """
    parts = path.split("__")
    lookup = parts[-1] if parts[-1] in {"isnull", "exact", "iexact", "in"} else ""
    if lookup:
        parts = parts[:-1]
    if not parts or any(part in NOT_SEATS for part in parts[:-1]):
        return False
    field_name = parts[-1]
    if field_name in {"player", "player_id"}:
        return lookup == "isnull" or (lookup in {"", "exact"} and _is_empty(value))
    if field_name == "guest_key":
        if lookup == "isnull":
            return True
        if lookup in {"", "exact", "iexact"}:
            return _is_empty(value)
        if lookup == "in" and isinstance(value, (ast.List, ast.Tuple, ast.Set)):
            return any(_is_empty(element) for element in value.elts)
    return False


def _held_query(node: ast.expr) -> bool:
    """A ``filter``, ``exclude``, ``get`` or ``Q`` with a condition that asks
    whether anybody holds a seat (spellings: :func:`_held_condition`)."""
    if not isinstance(node, ast.Call):
        return False
    if getattr(node.func, "id", getattr(node.func, "attr", None)) not in QUERY_CALLS:
        return False
    return any(
        _held_condition(keyword.arg, keyword.value)
        for keyword in node.keywords
        if keyword.arg is not None
    )


def _restates(node: ast.AST) -> list[ast.expr]:
    """The expressions in ``node`` that test a seat's holder fields."""
    tests: list[ast.expr] = []
    values: list[ast.expr] = []
    if isinstance(node, (ast.If, ast.IfExp, ast.While, ast.Assert)):
        tests.append(node.test)
    elif isinstance(node, ast.BoolOp):
        tests.extend(node.values)
    elif isinstance(node, ast.comprehension):
        tests.extend(node.ifs)
    elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.Return)) and node.value:
        values.append(node.value)
    elif isinstance(node, ast.Call):
        if _held_query(node):
            return [node]
        values.extend(node.args)
        values.extend(keyword.value for keyword in node.keywords)
    found = [test for test in tests if _holder_field(test) or _compares_to_empty(test)]
    found.extend(value for value in values if _compares_to_empty(value))
    return found


def restatements(module: Path, allowed_functions: frozenset[str]) -> list[str]:
    """Each place ``module`` tests a seat's holder fields itself.

    Args:
        module: The source file.
        allowed_functions: The functions in it that state the rule.

    Returns:
        ``"<path>:<line>"`` for each test found outside them.
    """
    tree = ast.parse(module.read_text(encoding="utf-8"))
    allowed: set[ast.AST] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in allowed_functions:
            allowed.update(ast.walk(node))
    return sorted(
        {
            f"{module.as_posix()}:{test.lineno}"
            for node in ast.walk(tree)
            if node not in allowed
            for test in _restates(node)
        }
    )


class TheRuleIsStatedOnceTest(SimpleTestCase):
    def test_no_module_writes_the_rule_out_again(self):
        root = Path(settings.BASE_DIR)
        modules = [
            path
            for app in ("battle", "gm")
            for path in sorted((root / app).rglob("*.py"))
            if "tests" not in path.parts
            and "migrations" not in path.parts
            and not path.name.startswith("test")
        ]
        found = [
            place
            for path in modules
            for place in restatements(
                path,
                STATEMENTS_OF_THE_RULE.get(
                    path.relative_to(root).as_posix(), frozenset()
                ),
            )
        ]
        self.assertEqual(
            found,
            [],
            "these test a seat's player or guest key themselves; ask "
            "battle.access.seat_is_held (or claimed_seat_q) instead. A receiver "
            f"named one of {sorted(NOT_SEATS)} is taken, by convention, not to "
            "be a seat.",
        )


class SeatsNameTheirHolderTest(BaseTestCase):
    """The lobby's rows and the pending choice name a seat's holder."""

    def setUp(self):
        super().setUp()
        self.game_master = self.create_dm(username="seat-gm")
        self.player = self.create_player(username="seat-player")
        self.lobby = create_battle(
            self.game_master, name="Lobby", count=1, seed=4, player_seats=3
        )
        self.assertEqual(self.lobby.status, Battle.STATUS_LOBBY)
        self.by_account, self.by_guest, self.empty = self.lobby.combatants.filter(
            is_open_seat=True
        )
        self.by_account.player = self.player
        self.by_account.save(update_fields=["player"])
        self.by_guest.guest_key = "a-guest"
        self.by_guest.save(update_fields=["guest_key"])
        self.expected = {
            self.by_account.pk: self.player.username,
            self.by_guest.pk: words().control.guest_owner,
            self.empty.pk: "",
        }

    def test_each_lobby_row_names_its_holder(self):
        rows = {
            row["combatant_id"]: row
            for row in seat_summaries(
                self.lobby, Actor(user=self.game_master, guest_key="")
            )
        }
        for seat_id, holder in self.expected.items():
            with self.subTest(seat=seat_id):
                self.assertEqual(rows[seat_id]["player"], holder)
        self.assertEqual(rows[self.empty.pk]["kind"], "open")

    def test_the_pending_choice_names_its_holder(self):
        spectator = Actor(
            user=self.create_player(username="seat-watcher"), guest_key=""
        )
        for seat_id, holder in self.expected.items():
            with self.subTest(seat=seat_id):
                self.lobby.pending_input_json = {
                    "combatant_id": seat_id,
                    "turn": 1,
                    "candidates": [],
                }
                payload = pending_payload(self.lobby, spectator)
                assert payload is not None
                self.assertEqual(payload["player"], holder)
