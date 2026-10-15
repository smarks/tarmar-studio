"""The strict reader every canon rules file shares (issue #59).

A canon carries its rules as JSON files beside its pages:
``magic-rules.json`` (``battle.magic_rules``) and, from issue #59,
``character-rules.json``. Each is read the same strict way, so a canon
author's mistake stops the server instead of becoming a quiet default:

- a missing or unreadable file, or one that is not JSON, is refused;
- a key repeated in one object is refused (``json.loads`` alone keeps the
  last and drops the first without a word);
- an object must carry every field its schema requires and nothing else,
  since an unknown key is most likely a misspelling of a known one;
- integers are not booleans, texts are not empty, and a list that stands
  for a set repeats nothing.

This module holds that machinery once. A rules module subclasses
:class:`RulesFileReader` with its own block parsers and error class, and
reads its file with :meth:`RulesFileReader.parsed_file` before handing the
data to its own parsers. Every refusal names the file and, for a field, the
field path, e.g. ``push.failure_bands[1].highest_margin``.
"""

from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, ClassVar, NoReturn, Protocol

from django.core.exceptions import ImproperlyConfigured

from common.helpers import parse_json_text


class RulesFileError(ImproperlyConfigured):
    """A canon rules file is missing or malformed.

    The message names the file and the field at fault. Each rules file has a
    subclass of its own (``battle.magic_rules.MagicRulesError``), so a caller
    may catch one file's refusal without the others'.
    """


def _refuse_repeated_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """``json.loads``'s object hook: one object, every key once.

    ``json.loads`` alone keeps the last of a repeated key, so a second
    ``"push"`` would replace the first without a word.

    Raises:
        ValueError: A key appears twice in one object.
    """
    keys = [key for key, _value in pairs]
    repeated = sorted({key for key in keys if keys.count(key) > 1})
    if repeated:
        raise ValueError(f"an object repeats the key {repeated}")
    return dict(pairs)


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
        """The whole file: a JSON object, checked as :meth:`json_object` does."""
        if not isinstance(value, dict):
            self.fail("the file", f"must hold a JSON object, got {value!r}")
        return self.json_object(value, "", required_fields, optional_fields)

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

    def optional_integer(self, value: Any, field_path: str) -> int | None:
        """An integer or ``null``."""
        if value is None:
            return None
        if not isinstance(value, int) or isinstance(value, bool):
            self.fail(field_path, f"must be an integer or null, got {value!r}")
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

    def integer_set(self, value: Any, field_path: str) -> frozenset[int]:
        """A list of integers with no repeats."""
        items = self.json_list(value, field_path)
        integers = [
            self.integer(item, f"{field_path}[{index}]")
            for index, item in enumerate(items)
        ]
        if len(set(integers)) != len(integers):
            self.fail(field_path, f"repeats a total: {integers}")
        return frozenset(integers)

    def text_list(self, value: Any, field_path: str) -> tuple[str, ...]:
        """A list of non-empty strings with no repeats."""
        items = self.json_list(value, field_path)
        texts = tuple(
            self.text(item, f"{field_path}[{index}]")
            for index, item in enumerate(items)
        )
        if len(set(texts)) != len(texts):
            self.fail(field_path, f"repeats an entry: {list(texts)}")
        return texts

    def unique_keys(self, items: Sequence[Keyed], field_path: str) -> None:
        """Entries whose ``key``s are all different."""
        keys = [item.key for item in items]
        repeated = sorted({key for key in keys if keys.count(key) > 1})
        if repeated:
            self.fail(field_path, f"repeats the key {repeated}")

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
