"""Every paged list orders its rows completely (issue #950).

A pager slices a queryset with OFFSET and LIMIT, and a database promises a
stable slice only for an ORDER BY that leaves no ties. The deleted-characters
page ordered by creation time alone, and the character list, the party list
and the bestiary catalog by a key two rows can share, so a row could sit on
two pages or on none from one request to the next. Each ends in a unique key
now.
"""

import importlib
import importlib.util
import re
from datetime import timedelta
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.db.models import QuerySet
from django.test import RequestFactory
from django.urls import URLPattern, URLResolver, get_resolver, reverse
from django.utils import timezone
from django.views.generic.list import MultipleObjectMixin

from bestiary.views import CatalogView
from characters.models import Character
from common.pagination import PAGE_SIZE
from common.pickers import PickerSource
from common.test_helpers import BaseTestCase, create_character

#: The ordering terms that make a total order on their own.
UNIQUE_KEYS = frozenset({"pk", "-pk", "id", "-id", "username", "-username"})

#: Every list a URL serves with ``paginate_by``, at the time of writing. The
#: discovery below finds them itself; this only proves it finds at least these.
KNOWN_PAGED_VIEWS = frozenset(
    {
        "BattleListView",
        "CatalogView",
        "CharacterListView",
        "DeletedCharactersView",
        "GMReviewListView",
        "PartyListView",
        "UsersListView",
    }
)

#: Every searchable picker source (#864, #973), as "<app>.<name>", at the time
#: of writing. The discovery below finds them itself; this only proves it
#: finds at least these.
KNOWN_PICKER_SOURCES = frozenset(
    {
        "battle.CLAIM_SOURCE",
        "battle.PARTY_SOURCE",
        "battle.SHELF_SOURCE",
        "characters.OWNER_FILTER_SOURCE",
        "gm.ATTACHABLE_SOURCE",
        "gm.PLAY_SESSION_SOURCE",
        "gm.SESSION_MENU_SOURCE",
        "gm.SESSION_RAIL_SOURCE",
        "parties.ADDABLE_SOURCE",
        "ruins.HOOK_PARTY_SOURCE",
    }
)

#: Every ``Paginator(`` in the project's own code, by file, and the test that
#: holds its ordering to a unique key. A new one fails
#: :meth:`test_every_paginator_call_is_held_to_a_unique_key` until it is added.
PAGINATOR_CALLS_CHECKED = {
    "bestiary/views.py": "test_the_generated_variants_end_in_a_unique_key",
    "common/pickers.py": "test_every_picker_source_ends_in_a_unique_key",
}


def last_ordering_term(queryset: QuerySet) -> str:
    """The queryset's last ORDER BY term, as ``name`` or ``-name``."""
    ordering = list(queryset.query.order_by) or list(
        queryset.model._meta.ordering or ()
    )
    last = ordering[-1]
    name = getattr(getattr(last, "expression", None), "name", None)
    if name is None:
        return str(last)
    return f"-{name}" if last.descending else name


def paged_views(patterns=None) -> dict[str, type]:
    """Every class-based view a URL serves whose list is paged, by name."""
    found: dict[str, type] = {}
    for pattern in patterns if patterns is not None else get_resolver().url_patterns:
        if isinstance(pattern, URLResolver):
            found.update(paged_views(pattern.url_patterns))
        elif isinstance(pattern, URLPattern):
            view_class = getattr(pattern.callback, "view_class", None)
            if (
                view_class is not None
                and issubclass(view_class, MultipleObjectMixin)
                and view_class.paginate_by
            ):
                found[view_class.__name__] = view_class
    return found


class EveryPagedListEndsInAUniqueKeyTest(BaseTestCase):
    """The class sweep, found rather than listed: every paged list."""

    def test_the_discovery_finds_the_known_paged_views(self):
        self.assertLessEqual(KNOWN_PAGED_VIEWS, set(paged_views()))

    def test_each_paged_queryset_ends_in_a_unique_key(self):
        viewer = self.create_admin()
        for name, view_class in sorted(paged_views().items()):
            with self.subTest(view=name):
                request = RequestFactory().get("/")
                request.user = viewer
                view = view_class()
                view.setup(request)
                self.assertIn(last_ordering_term(view.get_queryset()), UNIQUE_KEYS)

    def test_the_generated_variants_end_in_a_unique_key(self):
        request = RequestFactory().get("/")
        request.user = self.create_admin()
        view = CatalogView()
        view.setup(request)
        self.assertIn(last_ordering_term(view.generated_variants("")), UNIQUE_KEYS)

    def test_every_picker_source_ends_in_a_unique_key(self):
        """Every searchable picker (#864), found in each app's ``pickers``."""
        sources = {
            f"{config.name}.{name}": value
            for config in apps.get_app_configs()
            if importlib.util.find_spec(f"{config.name}.pickers") is not None
            for name, value in vars(
                importlib.import_module(f"{config.name}.pickers")
            ).items()
            if isinstance(value, PickerSource)
        }
        self.assertLessEqual(KNOWN_PICKER_SOURCES, set(sources))
        for name, source in sorted(sources.items()):
            with self.subTest(source=name):
                self.assertIn(source.order_by[-1], UNIQUE_KEYS)

    def test_every_paginator_call_is_held_to_a_unique_key(self):
        """A ``Paginator(`` this test does not know is a list nobody checked."""
        root = Path(settings.BASE_DIR)
        calls = {
            str(path.relative_to(root))
            for package in root.iterdir()
            if package.is_dir()
            and not package.name.startswith(".")
            and package.name != "node_modules"
            for path in package.rglob("*.py")
            if "tests" not in path.parts
            and not path.name.startswith("test_")
            and re.search(r"\bPaginator\(", path.read_text(encoding="utf-8"))
        }
        self.assertEqual(calls, set(PAGINATOR_CALLS_CHECKED))
        for test_name in PAGINATOR_CALLS_CHECKED.values():
            self.assertTrue(hasattr(self, test_name), test_name)


class DeletedCharactersPagesTest(BaseTestCase):
    """The deleted-characters page: most recently deleted first, each once."""

    def test_each_deleted_character_appears_once_newest_deletion_first(self):
        owner = self.create_user("many-deleted")
        deleted_at = timezone.now()
        total = PAGE_SIZE + 7
        made = []
        for number in range(total):
            character = create_character(owner, name=f"Gone {number}")
            made.append(character.pk)
        # Deleted in the opposite order to the one they were made in, and the
        # first ten in one instant, so neither creation time nor deletion time
        # alone decides the order.
        for offset, pk in enumerate(made):
            when = deleted_at - timedelta(minutes=max(offset - 10, 0))
            Character.objects.filter(pk=pk).update(is_deleted=True, deleted_at=when)
        self.login_as(self.create_admin())

        seen = []
        for page in (1, 2):
            response = self.client.get(reverse("characters:deleted"), {"page": page})
            self.assertEqual(response.status_code, 200)
            seen.extend(row.pk for row in response.context["characters"])

        self.assertEqual(len(seen), total)
        self.assertEqual(sorted(seen), sorted(made))
        tied = sorted(made[:11], reverse=True)
        self.assertEqual(seen[:11], tied)
        self.assertEqual(seen[-1], made[-1])

    def test_a_row_deleted_before_deleted_at_was_kept_goes_last(self):
        owner = self.create_user("legacy-deleted")
        legacy = create_character(owner, name="Legacy")
        recent = create_character(owner, name="Recent")
        Character.objects.filter(pk=legacy.pk).update(is_deleted=True, deleted_at=None)
        Character.objects.filter(pk=recent.pk).update(
            is_deleted=True, deleted_at=timezone.now()
        )
        self.login_as(self.create_admin())

        response = self.client.get(reverse("characters:deleted"))

        self.assertEqual(
            [row.pk for row in response.context["characters"]],
            [recent.pk, legacy.pk],
        )
