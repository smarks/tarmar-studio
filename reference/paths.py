"""Where the canon markdown lives, derived once (issue #337).

Ten modules across five apps -- the reference views and their search index,
the GM's prompt and its retrieval corpus, the ruins tables and hooks, the
settings default for ``RULES_DIR``, the guide builder -- each need a path
under ``reference/content``, and each used to walk up from its own
``__file__`` to find it. That is the same fact restated ten times: rename the
directory and nine of them keep pointing at nothing until someone finds them
all.

The DM-only canon (the ``dm-rules/`` tree) is resolved separately, through
:func:`resolve_canon_dir`, so it can live outside this repository (issue
#917). Its URL prefix stays ``dm-rules/`` wherever the directory is: every
reader maps that prefix to :data:`DM_RULES_DIR` through
:func:`page_markdown_file` or :func:`iter_content_markdown` rather than
assuming the tree sits under :data:`CONTENT_DIR`.

Free of Django on purpose. ``gm.engine`` and ``gm.retrieval`` import from
here and both run under the offline CLI, which has no Django configured; the
``reference`` package's ``__init__`` is empty, so importing this module does
not pull an app in behind it. The one Django name used, ``ImproperlyConfigured``,
is imported only on the path that raises it, so a correct configuration loads
no Django module at all (``gm.tests`` holds ``gm.engine``'s import chain to
that).

A canon directory holds the ``dm-rules/`` pages plus eight JSON files and
the Games Master's world digest, all read once per process (issues #918,
#919, #960, #59, #70 and #75; ``reference.apps`` has ``runserver`` watch
them):

- ``canon.json`` (optional): ``{"name": "tarmar" | "default"}``, which canon
  this is, as :data:`CANON_NAME` reports it. Without the file a directory is
  the Tarmar canon, except :data:`DEFAULT_CANON_DIR`, which is the default.
- ``nav.json`` (required for any page beyond the landing page): a JSON list
  of the DM Rules section's nav items, each an object with string ``title``
  and ``slug``, optional boolean ``dm_only``, optional list of strings
  ``guides``, and optional list ``children`` of items of the same form.
  Read by ``reference.nav``.
- ``maps.json`` (required for the GM panel to have any map): an object with
  ``region`` (strings ``slug``, ``title``, ``image`` under
  ``static/images/``, ``gazetteer`` relative to the canon directory, and an
  optional list ``charts`` of ``{"section", "slug", "image"}`` strings, one
  per ``##`` section of the gazetteer's hub page), an optional ``world``
  (strings ``slug``, ``title``, ``image`` and a list of strings
  ``place_names``), and an optional list of strings
  ``case_sensitive_place_names``. Read by ``gm.maps``.
- ``magic-rules.json`` (required to seat a battle and to roll a caster's
  school): the magic-mechanics numbers the engine's ``MagicRules`` takes, one
  top-level field per mechanic; the studio's own ``schools`` block, the table
  a caster's school of magic is rolled on (issue #18), which the engine takes
  nothing from; and an optional ``rulings`` list recording readings the pages
  left open. Every field but ``schools`` and ``rulings`` is named exactly as
  the engine dataclass field it fills. Read by ``battle.magic_rules``, whose
  docstring gives the full schema.
- ``character-rules.json`` (required to create a character): the numbers
  character creation reads, as the public character pages state them: the
  attribute ceiling and point buy, the starting age and aging stage, the
  attribute, Fatigue and literacy rolls, the height and weight tables, and the
  provenance, location and wealth percentile tables, with an optional
  ``rulings`` list. Read by ``characters.rules``, whose docstring gives the
  full schema; the sheet's sentences and the Games Master's prompt state its
  numbers from there (issue #68).
- ``skill-rules.json`` (required: a canon without one fails the
  ``characters.E002`` system check): the numbers skills, prior experience and
  the checks read, as the public prior experience, attributes and skills pages
  state them: the skill tracks and their d20 tables, the experience ladder,
  the skill cost multipliers, the survival roll, the aging table, the
  attribute modifier table, and the skill and attribute checks, with an
  optional ``rulings`` list. Read by ``characters.skill_rules``, whose
  docstring gives the full schema; the sheet's sentences and the Games
  Master's prompt state its numbers from there (issue #68).
- ``equipment.json`` (required: a canon without one fails the
  ``characters.E003`` system check): the equipment catalog, the weapons,
  armour, shields and gear a character may buy, each with its price, weight
  and the fields its group carries (damage, strength requirement and weapon
  class; stops, DEX penalty, tier and movement restriction; shield bonus), as
  the public equipment pages list them. Read by ``characters.equipment_rules``,
  whose docstring gives the full schema; the Games Master's prompt lists its
  weapons, armour and shields from there (issue #68). The battle names some
  items by id (the archetypes' kits and step-downs, the arrows and the bows'
  class, the hand-to-hand weapon), which the canon must carry
  (``battle.E005``).
- ``equipment-rules.json`` (required: a canon without one fails the
  ``characters.E004`` system check): the numbers encumbrance and movement
  read, as the public movement and armour pages state them: the encumbrance
  levels with their multipliers, penalties, forbidden gaits and walk limit,
  the armour restriction codes, the gaits' order and base distances, the
  Missile weapon classes (the ones Archery covers and the battle seats as
  missile weapons) and the bounds on a defender's dodge,
  with an optional ``rulings`` list. Read by ``characters.equipment_rules``,
  whose docstring gives the full schema; the sheet's help and the Games
  Master's prompt state its levels and gaits from there (issue #68). The level
  keys and gait names are the canon's, but the battle engine seats exactly
  four gaits, the one it seats as
  its run above 0 (``battle.E003``, until stage 6 of rules-as-data seats the
  engine's own rules), and the battle generator deals no load past a level it
  names, which the canon must carry (``battle.E004``).
  The five rules files are read the one strict way ``reference.rules_file``
  holds.
- ``gm-prompt/world.md`` (optional): the world section of the Games Master's
  system prompt, used verbatim less its trailing newlines. Without it
  ``gm.engine`` uses a one-paragraph built-in fallback and logs a warning.
  It is prompt text, not a page: never served, searched or retrieved.
  Read by ``gm.engine``.
- ``gm-prompt/magic.md`` (optional): the magic section of the same prompt,
  read the same way (issue #922). Without it ``gm.engine`` uses the default
  canon's, which states only the public rules, and logs a warning. Its
  Special Casting Results and Borrowing headings carry no body: ``gm.engine``
  builds both from ``magic-rules.json`` and refuses a file that writes one
  (issue #48).

Four more entries a canon may carry are not pages either, so
:func:`iter_content_markdown` skips them like ``gm-prompt/`` (issue #921;
the Tarmar canon, a repository of its own, has all four): ``author/``, the
author's working documents and ``forbidden-terms.txt``, the guide leak
check's term list; ``guides/``, the Game Master's guide and the private
cosmology notes that ``scripts/build_guides.py`` writes there when it runs
against a Tarmar canon; ``tests/expectations.json``, the names and facts the
Tarmar-only tests pin, read by ``common.test_helpers``; and the
repository's ``README.md``.

Production must name its canon: with ``DEBUG`` off outside a test run,
``tarmar_studio.settings`` logs a warning through
:func:`warn_if_production_lacks_its_canon` when the default canon was
resolved, since the Tarmar pages, maps and prompt are then absent.

``TARMAR_CANON_DIR`` is read when this module is first imported, and
``tarmar_studio.settings`` imports it before it loads ``.env``: the variable
has to be in the real environment (systemd's ``EnvironmentFile`` in
production), not only in a dotenv file.
"""

import json
import logging
import os
from collections.abc import Iterator, Mapping
from pathlib import Path, PurePosixPath
from typing import NoReturn

# reference/paths.py -> reference/ -> repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent

# Wording someone may change with no Python, one text file each, read by
# ``common.config_text.config_lines``: the passphrase gate's hints (settings'
# GATE_HINTS_FILE) and what a canon rules file's refusal of an unknown block
# adds (``reference.rules_file``, issue #36).
CONFIG_DIR = REPO_ROOT / "config"

# The canon corpus: every markdown page the site serves and the GM reads.
CONTENT_DIR = REPO_ROOT / "reference" / "content"

# The player-facing ruleset -- the GM's default prompt scope, and small
# enough to fit the model's context window.
PUBLIC_RULES_DIR = CONTENT_DIR / "public-rules"

# Names the directory holding the DM-only canon when it lives elsewhere.
CANON_DIR_ENV = "TARMAR_CANON_DIR"

# The URL prefix (and the relative-path prefix in the search index and the
# retrieval corpus) under which the canon is served, wherever it lives.
CANON_URL_PREFIX = "dm-rules"

# The canon to fall back on when neither the environment nor this checkout
# supplies one: a small generic world shipped in this repository (issue #918).
DEFAULT_CANON_DIR = REPO_ROOT / "reference" / "default_canon"

# CANON_NAME values: the full Tarmar canon, or the stand-in default canon.
TARMAR_CANON_NAME = "tarmar"
DEFAULT_CANON_NAME = "default"

# How the GM chat page and the reference pages name the loaded canon (issue
# #918), so nobody mistakes the generic world for the real one. Its keys are
# the names a canon may give itself.
CANON_LABELS: dict[str, str] = {
    TARMAR_CANON_NAME: "Tarmar canon",
    DEFAULT_CANON_NAME: "default canon",
}

# Three of the JSON files a canon directory carries beside its pages (see
# above).
CANON_MANIFEST_FILE_NAME = "canon.json"
CANON_NAV_FILE_NAME = "nav.json"
CANON_MAPS_FILE_NAME = "maps.json"
# The canon JSON files the reference search index fingerprints
# (``reference.search_index``): an edit to one rebuilds the index.
# ``magic-rules.json``, ``character-rules.json``, ``skill-rules.json``,
# ``equipment.json`` and ``equipment-rules.json`` are not among them, since no
# page or nav entry depends on any of them.
CANON_JSON_FILE_NAMES: tuple[str, ...] = (
    CANON_MANIFEST_FILE_NAME,
    CANON_NAV_FILE_NAME,
    CANON_MAPS_FILE_NAME,
)


class PathOutsideRoot(ValueError):
    """A requested file that resolves outside the directory it must stay in."""


def canon_name_of(canon_directory: Path, fallback: str) -> str:
    """The name a canon directory gives itself in ``canon.json``.

    Args:
        canon_directory: The canon directory.
        fallback: The name to use when the directory has no ``canon.json``.

    Returns:
        A key of :data:`CANON_LABELS`.

    Raises:
        ImproperlyConfigured: ``canon.json`` is not a JSON object whose
            ``name`` is one of :data:`CANON_LABELS`.
    """
    manifest = canon_directory / CANON_MANIFEST_FILE_NAME
    if not manifest.is_file():
        return fallback
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        _raise_improperly_configured(f"{manifest} is not valid JSON: {error}")
    name = data.get("name") if isinstance(data, dict) else None
    if name not in CANON_LABELS:
        _raise_improperly_configured(
            f"{manifest}: name must be one of {sorted(CANON_LABELS)}, got {name!r}"
        )
    return str(name)


def _raise_improperly_configured(message: str) -> NoReturn:
    """Raise Django's ``ImproperlyConfigured``, importing it only here."""
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(message)


def resolve_canon(
    environ: Mapping[str, str] | None = None, content_dir: Path | None = None
) -> tuple[Path, str]:
    """Find the canon directory and name which canon it is.

    In order: ``TARMAR_CANON_DIR`` when set (it must be the absolute path of
    an existing directory; a relative one would depend on the working
    directory the process happened to start in); else
    ``<content_dir>/dm-rules`` when that exists; else
    :data:`DEFAULT_CANON_DIR`, whether or not it exists yet.

    Args:
        environ: Where to read ``TARMAR_CANON_DIR`` (default ``os.environ``).
            A blank value counts as unset.
        content_dir: The served content tree (default :data:`CONTENT_DIR`).

    The name is the directory's own ``canon.json`` when it has one, else
    :data:`DEFAULT_CANON_NAME` for :data:`DEFAULT_CANON_DIR` (however it was
    reached) and :data:`TARMAR_CANON_NAME` for anything else.

    Returns:
        The canon directory and its name, :data:`TARMAR_CANON_NAME` or
        :data:`DEFAULT_CANON_NAME`.

    Raises:
        ImproperlyConfigured: ``TARMAR_CANON_DIR`` is set but is not the
            absolute path of an existing directory, or the chosen canon's
            ``canon.json`` is malformed (not JSON, not an object, or a
            ``name`` outside :data:`CANON_LABELS`). ``TARMAR_CANON_DIR``
            naming :data:`DEFAULT_CANON_DIR` is not an error: it resolves to
            the default canon under :data:`DEFAULT_CANON_NAME`.
    """
    source = os.environ if environ is None else environ
    content_root = CONTENT_DIR if content_dir is None else content_dir
    configured_directory = source.get(CANON_DIR_ENV, "").strip()
    if configured_directory:
        configured_path = Path(configured_directory)
        if not configured_path.is_absolute() or not configured_path.is_dir():
            _raise_improperly_configured(
                f"{CANON_DIR_ENV} is set to {configured_directory!r}, which is "
                f"not the absolute path of an existing directory."
            )
        canon_directory = configured_path.resolve()
        fallback = (
            DEFAULT_CANON_NAME
            if canon_directory == DEFAULT_CANON_DIR.resolve()
            else TARMAR_CANON_NAME
        )
        return canon_directory, canon_name_of(canon_directory, fallback)
    in_tree_canon = content_root / CANON_URL_PREFIX
    if in_tree_canon.is_dir():
        return in_tree_canon, canon_name_of(in_tree_canon, TARMAR_CANON_NAME)
    return DEFAULT_CANON_DIR, canon_name_of(DEFAULT_CANON_DIR, DEFAULT_CANON_NAME)


def resolve_canon_dir(
    environ: Mapping[str, str] | None = None, content_dir: Path | None = None
) -> Path:
    """The canon directory, without its name; see :func:`resolve_canon`.

    Args:
        environ: Where to read ``TARMAR_CANON_DIR`` (default ``os.environ``).
        content_dir: The served content tree (default :data:`CONTENT_DIR`).

    Returns:
        The directory served under the ``dm-rules/`` prefix.

    Raises:
        ImproperlyConfigured: ``TARMAR_CANON_DIR`` is set but is not the
            absolute path of an existing directory, or the chosen canon's
            ``canon.json`` is malformed. ``TARMAR_CANON_DIR`` naming
            :data:`DEFAULT_CANON_DIR` is not an error.
    """
    canon_directory, _canon_name = resolve_canon(environ, content_dir)
    return canon_directory


def warn_if_production_lacks_its_canon(
    canon_name: str, environ: Mapping[str, str] | None = None
) -> bool:
    """Log a warning that a production process loaded the default canon.

    ``tarmar_studio.settings`` calls this once it knows ``DEBUG`` is off
    outside a test run, which is where the answer is known: ``DEBUG`` may
    come from ``.env``, which is read after this module is imported. Logged
    through :mod:`logging`, so this module stays free of Django.

    Args:
        canon_name: The resolved canon's name.
        environ: Where to read ``TARMAR_CANON_DIR`` (default ``os.environ``),
            to say whether it was unset or named the default canon.

    Returns:
        Whether the warning was logged: only for the default canon.
    """
    if canon_name != DEFAULT_CANON_NAME:
        return False
    source = os.environ if environ is None else environ
    configured_directory = source.get(CANON_DIR_ENV, "").strip()
    cause = (
        f"{CANON_DIR_ENV} names the default canon ({configured_directory})"
        if configured_directory
        else f"{CANON_DIR_ENV} is not set"
    )
    logging.getLogger(__name__).warning(
        "%s, so this production process (DEBUG off) loaded the generic default "
        "canon from %s: no Tarmar pages, maps or world prompt. Set %s in .env "
        "to the Tarmar canon checkout (DEPLOY.md).",
        cause,
        DEFAULT_CANON_DIR,
        CANON_DIR_ENV,
    )
    return True


# The GM's own material, including the world gazetteer: the tree served under
# the dm-rules/ URL prefix.
DM_RULES_DIR, CANON_NAME = resolve_canon()
CANON_LABEL = CANON_LABELS[CANON_NAME]

# Generator source pages: the dice tables and hook lists that ``ruins`` parses
# at runtime rather than restating in code.
GM_TOOLS_DIR = DM_RULES_DIR / "gm-tools"

# The canon's prompt text (issue #919): files under ``gm-prompt/`` are read
# into the Games Master's system prompt by ``gm.engine``. They are prompt
# text, not reference pages, so :func:`iter_content_markdown` never yields
# them: neither the search index nor the retrieval corpus sees them.
CANON_PROMPT_DIR_NAME = "gm-prompt"
WORLD_PROMPT_FILE = DM_RULES_DIR / CANON_PROMPT_DIR_NAME / "world.md"

# The magic section (issue #922): the Tarmar canon's carries the GM-side
# numbers the public magic pages leave out, so it lives with the canon too. A
# canon without one gets the default canon's, which keeps to the public rules.
MAGIC_PROMPT_FILE = DM_RULES_DIR / CANON_PROMPT_DIR_NAME / "magic.md"
DEFAULT_MAGIC_PROMPT_FILE = DEFAULT_CANON_DIR / CANON_PROMPT_DIR_NAME / "magic.md"

# The magic rules battles are seated with (issue #960): the casting success
# tiers, Push and Channel, read into the engine's ``MagicRules`` by
# ``battle.magic_rules``. Every canon carries one; the default canon's holds
# invented numbers.
CANON_MAGIC_RULES_FILE_NAME = "magic-rules.json"
MAGIC_RULES_FILE = DM_RULES_DIR / CANON_MAGIC_RULES_FILE_NAME

# The character-creation rules (issue #59): the point buy, the aging stages,
# the attribute, Fatigue and literacy rolls, the height and weight tables and the social
# tables, read the same strict way through ``reference.rules_file`` by
# ``characters.rules``. Every canon carries one; the default canon's holds the
# public character pages' numbers.
CANON_CHARACTER_RULES_FILE_NAME = "character-rules.json"
CHARACTER_RULES_FILE = DM_RULES_DIR / CANON_CHARACTER_RULES_FILE_NAME

# The skills and experience rules (issue #70): the skill tracks, the
# experience ladder, the skill cost, the survival roll, the aging table, the
# attribute modifiers and the skill and attribute checks, read the same strict
# way by ``characters.skill_rules``. Every canon carries one; the default
# canon's holds the public pages' numbers.
CANON_SKILL_RULES_FILE_NAME = "skill-rules.json"
SKILL_RULES_FILE = DM_RULES_DIR / CANON_SKILL_RULES_FILE_NAME

# The equipment catalog and the equipment rules (issue #75): the items a
# character may buy, and the encumbrance levels, armour restriction codes,
# gaits, Missile classes and dodge bounds, read the same strict way by
# ``characters.equipment_rules``. Every canon carries both; the default
# canon's hold the public pages' items and numbers.
CANON_EQUIPMENT_CATALOG_FILE_NAME = "equipment.json"
EQUIPMENT_CATALOG_FILE = DM_RULES_DIR / CANON_EQUIPMENT_CATALOG_FILE_NAME
CANON_EQUIPMENT_RULES_FILE_NAME = "equipment-rules.json"
EQUIPMENT_RULES_FILE = DM_RULES_DIR / CANON_EQUIPMENT_RULES_FILE_NAME

# The rest of what a canon directory may hold beside its pages (issue #921):
# the author's documents, the guides generated from the canon, the Tarmar-only
# tests' expectations, and the canon repository's README. Like
# ``gm-prompt/``, none of them is a page.
CANON_AUTHOR_DIR_NAME = "author"
CANON_GUIDES_DIR_NAME = "guides"
CANON_TESTS_DIR_NAME = "tests"
CANON_README_NAME = "README.md"
CANON_NON_PAGE_NAMES: frozenset[str] = frozenset(
    {
        CANON_PROMPT_DIR_NAME,
        CANON_AUTHOR_DIR_NAME,
        CANON_GUIDES_DIR_NAME,
        CANON_TESTS_DIR_NAME,
        CANON_README_NAME,
    }
)


def canon_dir_for(content_root: Path) -> Path:
    """The directory the ``dm-rules/`` prefix maps to under ``content_root``.

    The served tree (:data:`CONTENT_DIR`) takes its canon from
    :data:`DM_RULES_DIR`. Any other root -- a test corpus, a ``GM_CORPUS_ROOT``
    override -- keeps its own ``dm-rules/`` subdirectory, as it always did.

    Args:
        content_root: The content tree being read.

    Returns:
        The directory whose files take the ``dm-rules/`` relative paths.
    """
    if content_root.resolve() == CONTENT_DIR.resolve():
        return DM_RULES_DIR
    return content_root / CANON_URL_PREFIX


def page_markdown_file(page_path: str) -> tuple[Path, Path]:
    """The markdown file a reference page path names, and the root it is under.

    ``dm-rules/<rest>`` is ``<DM_RULES_DIR>/<rest>.md``; every other path,
    the ``dm-rules`` landing page included, is ``<CONTENT_DIR>/<path>.md``.
    A ``..`` segment is refused before the prefix is read, so
    ``public-rules/../dm-rules/x`` cannot reach the in-repo ``dm-rules/`` by
    way of the content root. The file is not resolved: a caller that reads
    it passes it and the returned root to :func:`file_inside`, which follows
    symlinks and refuses a file outside that root.

    Args:
        page_path: The page's slug path, as the reference URL carries it.

    Returns:
        The markdown file and the root directory it must stay inside.

    Raises:
        ValueError: ``page_path`` has a ``..`` segment.
    """
    if ".." in page_path.split("/"):
        raise ValueError(f"Page path climbs out of its directory: {page_path!r}")
    prefix, separator, remainder = page_path.partition("/")
    if prefix == CANON_URL_PREFIX and separator:
        return DM_RULES_DIR / f"{remainder}.md", DM_RULES_DIR
    return CONTENT_DIR / f"{page_path}.md", CONTENT_DIR


def file_inside(root: Path, requested_file: str | Path) -> Path:
    """``requested_file`` resolved, refused unless it stays inside ``root``.

    The one containment check for a file named by a request: a reference page
    path, a table roller's slug. Both paths are made real (symlinks followed,
    ``..`` collapsed) with :func:`os.path.realpath`, and the file must then
    sit below the real root, so neither a ``..`` segment, an absolute path nor
    a symlink pointing out can reach a file elsewhere. The comparison is a
    string prefix on the real paths with the separator appended, so a sibling
    such as ``content-old`` does not pass for ``content``.

    Args:
        root: The directory the file must stay inside.
        requested_file: The file, relative to ``root`` or absolute.

    Returns:
        The file's real path. It need not exist.

    Raises:
        PathOutsideRoot: The resolved file is not below ``root``.
    """
    real_root = os.path.realpath(root)
    real_file = os.path.realpath(os.path.join(real_root, requested_file))
    if not real_file.startswith(real_root + os.sep):
        raise PathOutsideRoot(f"{requested_file!s} is outside {root}")
    return Path(real_file)


def iter_content_markdown(
    content_root: Path | None = None,
) -> Iterator[tuple[PurePosixPath, Path]]:
    """Every markdown file in a content tree, with the canon mapped in.

    The ``dm-rules/`` subtree of ``content_root`` is replaced by
    :func:`canon_dir_for`'s directory: when the canon lives elsewhere, the
    copy under ``content_root`` is not read at all. Files come in order of
    their relative path, compared part by part, which is the order sorting the
    absolute paths of the in-tree layout gave.

    The canon's entries in :data:`CANON_NON_PAGE_NAMES` are left out:
    ``gm-prompt/`` is prompt text for ``gm.engine`` (issue #919), and
    ``author/``, ``guides/`` and ``README.md`` are documents about the canon
    (issue #921), not pages.

    Args:
        content_root: The tree to walk (default :data:`CONTENT_DIR`).

    Yields:
        ``(relative path, file)`` pairs; the relative path is the one the
        file has in the served layout, e.g. ``dm-rules/the-world.md``.
    """
    root = CONTENT_DIR if content_root is None else content_root
    canon_directory = canon_dir_for(root)
    files: list[tuple[PurePosixPath, Path]] = []
    for markdown_file in root.rglob("*.md"):
        relative_path = PurePosixPath(markdown_file.relative_to(root).as_posix())
        if relative_path.parts[0] == CANON_URL_PREFIX:
            continue
        files.append((relative_path, markdown_file))
    if canon_directory.is_dir():
        for markdown_file in canon_directory.rglob("*.md"):
            canon_relative_path = PurePosixPath(
                markdown_file.relative_to(canon_directory).as_posix()
            )
            if canon_relative_path.parts[0] in CANON_NON_PAGE_NAMES:
                continue
            relative_path = PurePosixPath(CANON_URL_PREFIX) / canon_relative_path
            files.append((relative_path, markdown_file))
    files.sort(key=lambda entry: entry[0])
    yield from files
