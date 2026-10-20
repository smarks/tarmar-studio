"""The strict reader every canon rules file shares (issue #59).

A canon carries its rules as JSON files beside its pages:
``magic-rules.json`` (``battle.magic_rules``), from issue #59
``character-rules.json`` (``characters.rules``), from issue #70
``skill-rules.json`` (``characters.skill_rules``) and from issue #75
``equipment.json`` and ``equipment-rules.json``
(``characters.equipment_rules``). Each is read the same strict way, so a
canon author's mistake stops the server instead of becoming a quiet default:

- a missing or unreadable file, or one that is not JSON, is refused;
- a key repeated in one object is refused (``json.loads`` alone keeps the
  last and drops the first without a word);
- an object must carry every field its schema requires and nothing else,
  since an unknown key is most likely a misspelling of a known one;
- integers and numbers are not booleans, texts are not empty, and a list
  that stands for a set repeats nothing;
- every file may carry an optional top-level ``rulings`` list, the canon's
  own record of the readings its pages left open: objects with a non-empty
  string ``key`` and ``reading`` and whatever else the author records
  (``page``, ``for_spencer``), checked for shape and not otherwise read.

This module holds that machinery once. A rules module subclasses
:class:`RulesFileReader` with its own block parsers and error class, and
reads its file with :meth:`RulesFileReader.parsed_file` before handing the
data to its own parsers. Every refusal names the file and, for a field, the
field path, e.g. ``push.failure_bands[1].highest_margin``.

Free of Django on purpose, like ``reference.paths``: ``characters.dice`` is in
``gm.engine``'s import chain, which the offline CLI imports with no Django
configured, and ``characters.rules``, which reads its file through here, is
where ``characters.dice`` takes its numbers from once issue #59 seats them;
``characters.skill_rules`` and ``characters.equipment_rules`` are read the
same way (``gm.tests``' ``test_engine_imports_without_django`` holds them all
to that).

A value built from a loaded rules file and then cached (a Games Master prompt,
a pattern naming the canon's skills) registers its cache with
:func:`derived_from_rules`, so :func:`forget_values_derived_from_rules` can
clear every one of them when a test seats other rules (issue #70).
"""

import math
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, ClassVar, NoReturn, Protocol

from common.json_text import parse_json_text

#: The optional top-level field every rules file may carry (see above).
RULINGS_FIELD = "rulings"
#: The fields each entry of ``rulings`` must carry as non-empty strings.
RULING_FIELDS = ("key", "reading")


class CacheCounts(Protocol):
    """What a cache reports of itself: ``functools.cache``'s ``cache_info()``."""

    @property
    def misses(self) -> int: ...

    @property
    def currsize(self) -> int: ...


class RulesDerivedCache(Protocol):
    """A cached function of no arguments: ``functools.cache``'s wrapper, for one.

    No arguments, because the value is the rules' alone: the same for every
    caller until the rules change.
    """

    def __call__(self) -> object: ...

    def cache_clear(self) -> None: ...

    def cache_info(self) -> CacheCounts: ...


#: Every cache :func:`derived_from_rules` registered, in registration order.
_CACHES_DERIVED_FROM_RULES: list[RulesDerivedCache] = []


def derived_from_rules[Cached: RulesDerivedCache](cached: Cached) -> Cached:
    """Register a cached function whose value is built from loaded rules.

    Placed above ``@functools.cache``, so the registered cache is the one the
    function's callers read. A module registers its caches when it is
    imported; a cache whose module was never imported holds nothing to clear.

    Args:
        cached: The cached function.

    Returns:
        ``cached``, unchanged.
    """
    _CACHES_DERIVED_FROM_RULES.append(cached)
    return cached


def caches_derived_from_rules() -> tuple[RulesDerivedCache, ...]:
    """Every cache :func:`derived_from_rules` has registered so far."""
    return tuple(_CACHES_DERIVED_FROM_RULES)


def forget_values_derived_from_rules() -> None:
    """Clear every registered cache, so each value is built afresh when asked.

    The loaded rules files themselves are cached by their own modules
    (``characters.skill_rules.skill_rules`` and its siblings), which a caller
    seating other rules clears beside this.
    """
    for cached in _CACHES_DERIVED_FROM_RULES:
        cached.cache_clear()


class RulesFileError(Exception):
    """A canon rules file is missing or malformed.

    The message names the file and the field at fault. Each rules file has a
    subclass of its own (``battle.magic_rules.MagicRulesError``), so a caller
    may catch one file's refusal without the others'.

    A plain ``Exception``: not Django's ``ImproperlyConfigured``, which would
    pull Django into this module, and not a ``ValueError``, which
    :meth:`RulesFileReader.wrapped_value_errors` would wrap a second time.
    """


def repeated_values(values: Iterable[str]) -> list[str]:
    """Every value given more than once, each named once, in sorted order.

    The one "nothing repeated" check (issue #76): :func:`repeats_problem`
    words it for the rules files, and ``gm.maps`` words it for the map
    manifest's slugs.

    Args:
        values: The values, in any order; read once, so an iterator will do.

    Returns:
        The repeated values, sorted; empty when no value appears twice.
    """
    return sorted(
        value for value, times_given in Counter(values).items() if times_given > 1
    )


def repeats_problem(kind: str, values: Iterable[str]) -> str | None:
    """The refusal for values that should each appear once, or ``None``.

    The wording every rules file shares for :func:`repeated_values`: the
    reader's object hook and :meth:`RulesFileReader.unique_keys` use it, and
    so does each rules dataclass whose ``__post_init__`` refuses a repeated
    label or key, raising it as a ``ValueError`` for
    :meth:`RulesFileReader.wrapped_value_errors` to name by field path.

    Args:
        kind: What the values are, as the refusal names them (``"key"``).
        values: The values, in any order.

    Returns:
        ``"repeats the <kind> [...]"``, naming each repeated value once and
        in sorted order, or ``None`` when no value appears twice.
    """
    repeated = repeated_values(values)
    if not repeated:
        return None
    return f"repeats the {kind} {repeated}"


def _refuse_repeated_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """``json.loads``'s object hook: one object, every key once.

    ``json.loads`` alone keeps the last of a repeated key, so a second
    ``"push"`` would replace the first without a word.

    Raises:
        ValueError: A key appears twice in one object.
    """
    if problem := repeats_problem("key", (key for key, _value in pairs)):
        raise ValueError(f"an object {problem}")
    return dict(pairs)


def _is_finite_number(value: Any) -> bool:
    """Whether ``value`` is an ``int`` or a finite ``float``, not a ``bool``.

    ``json.loads`` reads the non-standard ``NaN`` and ``Infinity`` as floats,
    which no comparison with a bound would refuse.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    return math.isfinite(value)


class Keyed(Protocol):
    """An entry of a list whose entries are told apart by ``key``.

    A property, so a frozen dataclass's read-only ``key`` field matches.
    """

    @property
    def key(self) -> str: ...


class RulesFileReader:
    """Checks one rules file's values, naming the file in every error.

    Each method takes the value and its field path in the file and returns
    the value in the type the caller takes, or raises :attr:`error_class`.
    A subclass adds the parsers for its own file's blocks and sets
    :attr:`error_class` to its own :class:`RulesFileError` subclass.

    Attributes:
        error_class: The error every refusal raises.
        path: The file being read, named in every error.
    """

    error_class: ClassVar[type[RulesFileError]] = RulesFileError

    def __init__(self, path: Path) -> None:
        self.path = path

    def fail(self, field_path: str, problem: str) -> NoReturn:
        """Raise the error for one field.

        Args:
            field_path: Where in the file the problem is, or ``"the file"``.
            problem: What is wrong there.

        Raises:
            RulesFileError: Always, as :attr:`error_class`.
        """
        raise self.error_class(f"{self.path}: {field_path} {problem}")

    def parsed_file(self) -> Any:
        """The file's JSON, before any field is read.

        Returns:
            The parsed JSON value, for the subclass's own parsers to read.

        Raises:
            RulesFileError: As :attr:`error_class`: the file is missing or
                unreadable, is not JSON, or repeats a key in one object.
        """
        if not self.path.is_file():
            self.fail("the file", "is missing; every canon must carry one")
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError as error:
            raise self.error_class(f"{self.path} could not be read: {error}") from error
        try:
            return parse_json_text(text, object_pairs_hook=_refuse_repeated_keys)
        except ValueError as error:
            raise self.error_class(f"{self.path} is not valid JSON: {error}") from error

    @contextmanager
    def wrapped_value_errors(self, field_path: str) -> Iterator[None]:
        """Re-raise a ``ValueError`` from inside as this file's error.

        A dataclass that validates its own values in ``__post_init__`` (the
        engine's ``MagicRules`` and its parts, for one) raises ``ValueError``;
        building it inside this block names the file and the field instead.

        Args:
            field_path: The field whose values are built inside the block.

        Raises:
            RulesFileError: As :attr:`error_class`, when the block raised
                ``ValueError``.
        """
        try:
            yield
        except ValueError as error:
            raise self.error_class(f"{self.path}: {field_path}: {error}") from error

    def file_object(
        self,
        value: Any,
        required_fields: tuple[str, ...],
        optional_fields: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """The whole file: a JSON object, checked as :meth:`json_object` does.

        ``rulings`` is accepted beside ``optional_fields`` and, when present,
        checked by :meth:`rulings`.
        """
        if not isinstance(value, dict):
            self.fail("the file", f"must hold a JSON object, got {value!r}")
        fields = self.json_object(
            value, "", required_fields, (*optional_fields, RULINGS_FIELD)
        )
        if RULINGS_FIELD in fields:
            self.rulings(fields[RULINGS_FIELD], RULINGS_FIELD)
        return fields

    def json_object(
        self,
        value: Any,
        field_path: str,
        required_fields: tuple[str, ...],
        optional_fields: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """An object carrying every required field and nothing unknown."""
        if not isinstance(value, dict):
            self.fail(field_path, f"must be an object, got {value!r}")
        prefix = f"{field_path}." if field_path else ""
        for name in value:
            if name not in required_fields and name not in optional_fields:
                self.fail(f"{prefix}{name}", "is not a field this file takes")
        for name in required_fields:
            if name not in value:
                self.fail(f"{prefix}{name}", "is missing")
        return value

    def json_list(self, value: Any, field_path: str) -> list[Any]:
        """A list."""
        if not isinstance(value, list):
            self.fail(field_path, f"must be a list, got {value!r}")
        return value

    def integer(self, value: Any, field_path: str) -> int:
        """An integer; ``true`` and ``false`` are not integers here."""
        if not isinstance(value, int) or isinstance(value, bool):
            self.fail(field_path, f"must be an integer, got {value!r}")
        return value

    def integers(
        self, fields: dict[str, Any], field_path: str, names: tuple[str, ...]
    ) -> dict[str, int]:
        """The integer fields ``names`` of one object read by
        :meth:`json_object`, by name."""
        return {
            name: self.integer(fields[name], f"{field_path}.{name}") for name in names
        }

    def optional_integer(self, value: Any, field_path: str) -> int | None:
        """An integer or ``null``."""
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool):
            self.fail(field_path, f"must be an integer or null, got {value!r}")
        return value

    def number(self, value: Any, field_path: str) -> float:
        """A finite integer or decimal, kept as written (``3`` stays an
        integer); ``true``, ``false``, ``NaN`` and ``Infinity`` are not
        numbers here."""
        if not _is_finite_number(value):
            self.fail(field_path, f"must be a finite number, got {value!r}")
        return value

    def optional_number(self, value: Any, field_path: str) -> float | None:
        """A number, as :meth:`number` reads one, or ``null``."""
        if value is None:
            return None
        if not _is_finite_number(value):
            self.fail(field_path, f"must be a finite number or null, got {value!r}")
        return value

    def boolean(self, value: Any, field_path: str) -> bool:
        """``true`` or ``false``."""
        if not isinstance(value, bool):
            self.fail(field_path, f"must be true or false, got {value!r}")
        return value

    def text(self, value: Any, field_path: str) -> str:
        """A non-empty string."""
        if not isinstance(value, str) or not value:
            self.fail(field_path, f"must be a non-empty string, got {value!r}")
        return value

    def optional_text(self, value: Any, field_path: str) -> str | None:
        """A non-empty string or ``null``."""
        if value is None:
            return None
        if not isinstance(value, str) or not value:
            self.fail(field_path, f"must be a non-empty string or null, got {value!r}")
        return value

    def entries[Value](
        self,
        value: Any,
        field_path: str,
        read_entry: Callable[[Any, str], Value],
    ) -> tuple[Value, ...]:
        """A list, each entry read by ``read_entry`` at ``field_path[index]``."""
        return tuple(
            read_entry(item, f"{field_path}[{index}]")
            for index, item in enumerate(self.json_list(value, field_path))
        )

    def integer_list(self, value: Any, field_path: str) -> tuple[int, ...]:
        """A list of integers, in order; repeats allowed."""
        return self.entries(value, field_path, self.integer)

    def integer_set(self, value: Any, field_path: str) -> frozenset[int]:
        """A list of integers with no repeats."""
        integers = list(self.integer_list(value, field_path))
        if len(set(integers)) != len(integers):
            self.fail(field_path, f"repeats a total: {integers}")
        return frozenset(integers)

    def text_list(self, value: Any, field_path: str) -> tuple[str, ...]:
        """A list of non-empty strings with no repeats."""
        texts = self.entries(value, field_path, self.text)
        if len(set(texts)) != len(texts):
            self.fail(field_path, f"repeats an entry: {list(texts)}")
        return texts

    def unique_keys(self, items: Sequence[Keyed], field_path: str) -> None:
        """Entries whose ``key``s are all different."""
        if problem := repeats_problem("key", (item.key for item in items)):
            self.fail(field_path, problem)

    def mapping[Value](
        self,
        value: Any,
        field_path: str,
        read_entry: Callable[[Any, str], Value],
    ) -> dict[str, Value]:
        """An object of non-empty keys, each value read by ``read_entry``."""
        if not isinstance(value, dict):
            self.fail(field_path, f"must be an object, got {value!r}")
        entries: dict[str, Value] = {}
        for key, entry in value.items():
            if not key:
                self.fail(field_path, "has an empty key")
            entries[key] = read_entry(entry, f"{field_path}.{key}")
        return entries

    def rulings(self, value: Any, field_path: str) -> None:
        """``rulings``: a list of objects with string ``key`` and ``reading``."""
        for index, ruling in enumerate(self.json_list(value, field_path)):
            ruling_path = f"{field_path}[{index}]"
            if not isinstance(ruling, dict):
                self.fail(ruling_path, f"must be an object, got {ruling!r}")
            for name in RULING_FIELDS:
                self.text(ruling.get(name), f"{ruling_path}.{name}")
