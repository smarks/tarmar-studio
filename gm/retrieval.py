"""Retrieval over the canon corpus for the Game Master (issue #325).

The GM used to see only its fixed system prompt, plus — on ``/rules`` — the
whole ``public-rules`` tree pasted into one message. Nothing under ``dm-rules``
or the world pages ever reached it. This module grounds each ordinary chat turn
instead: the corpus under ``reference/content`` is cut into heading-sized
chunks, embedded once through Ollama's ``/api/embed`` into an on-disk JSON
index, and at chat time the top-k chunks for the player's message are handed to
the model as a "canon reference excerpts" message placed just before the
player's turn.

Why the excerpts persist (issue #331): Ollama's prompt cache only helps when
the new prompt begins with the previous one. #326 injected the excerpts as a
transient message and dropped them from the saved history, so every turn's
prefix differed from the last and the whole conversation was re-prefilled —
five to six seconds to first token on a 27B model. Each excerpt block is now a
real history entry (``kind: "canon"``, hidden from the UI) sitting in front of
the player's message, so the model-visible prefix only ever grows and the new
tokens per turn are the new block plus the player's message. Chunks the session
has already been shown are never re-injected, the block is bounded by a token
budget, and :func:`model_messages` drops the oldest blocks first when the
whole history would crowd out the reply.

Like :mod:`gm.engine`, this module must not import Django: the offline CLI
(``cli/gm.py``) uses it standalone. Every path, URL, and model name is a
parameter; the Django-aware defaults live in ``gm/services.py`` and
``tarmar_studio/settings.py``.
"""

import hashlib
import json
import logging
import math
import re
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from reference.paths import CONTENT_DIR, REPO_ROOT, iter_content_markdown

logger = logging.getLogger(__name__)

# The canon corpus: rules, DM material, and the world pages. ``proposals/``
# under it is not canon and is skipped by :func:`iter_corpus_chunks`. Its
# ``dm-rules/`` part is read from ``reference.paths.DM_RULES_DIR``, which may
# live outside this tree (issue #917); chunk sources keep the ``dm-rules/``
# prefix either way, so an existing index and hidden canon entries still match.
DEFAULT_CORPUS_ROOT = CONTENT_DIR
EXCLUDED_CORPUS_DIRS = frozenset({"proposals"})

# Runtime data lives under var/ (gitignored); the index is a few megabytes of
# JSON and is rebuilt by ``manage.py build_gm_index``.
DEFAULT_INDEX_PATH = REPO_ROOT / "var" / "gm-index.json"

DEFAULT_EMBED_MODEL = "nomic-embed-text"
DEFAULT_TOP_K = 6

# Budget for one turn's excerpt block, in estimated tokens (GM_RETRIEVAL_MAX_
# TOKENS). Retrieved chunks are taken in rank order until the next would not
# fit; the top chunk is always included.
DEFAULT_MAX_EXCERPT_TOKENS = 900

# Room kept free at the end of the context window for the model's reply when
# :func:`model_messages` decides whether old excerpt blocks must go.
REPLY_RESERVE_TOKENS = 2048

# Marker on a history entry carrying an excerpt block. The model sees such
# entries in order; the web page, the CLI transcript, and message counts skip
# them.
CANON_KIND = "canon"

# Marker on a history entry carrying the session's character sheets (issue
# #341; built by gm.character_sheet). Same lifecycle as a canon entry: filed
# ahead of the player's message, hidden from readers, stripped on the wire.
# Unlike canon blocks, a sheet is never aged out by the context guard.
SHEET_KIND = "sheet"

# Marker on a history entry carrying a hex-board battle's log lines or its
# closing summary (issue #345; built by gm.battle_report). Filed ahead of the
# player's message like the other two, hidden from readers, stripped on the
# wire, never aged out by the context guard.
BATTLE_KIND = "battle"

# Marker on a history entry carrying the session's character-creation ledger
# (issue #356; built by gm.ledger). Same lifecycle as a sheet entry: filed
# ahead of the player's message, hidden from readers, stripped on the wire,
# never aged out by the context guard. The ledger is the draft character's
# authoritative totals, computed in code, so the model never has to add
# anything up for itself.
LEDGER_KIND = "ledger"

# Marker on a history entry carrying a die the server rolled *for the GM*
# mid-reply (issue #363; built by gm.roll_service). Filed between the player's
# message and the GM's answer, because that is when the dice fell; hidden from
# readers as an entry (the page renders it as a dice line instead), stripped on
# the wire, never aged out by the context guard. It is the authoritative record
# of the faces, so a later "what did you roll?" is answered from it rather than
# guessed at.
ROLL_KIND = "roll"

# Marker on a history entry carrying what a turn's action did to a character
# (issues #370, #590; built by gm.turn_engine.history). Filed where a roll
# entry is, before the narration that describes it. Hidden from readers as an
# entry (the page renders its chip lines instead) and never sent to the model,
# which reads the character's records in each call's state block instead.
PROCEDURE_KIND = "procedure"

# Marker on a history entry recording that the session changed mode (issue
# #372; built by gm.messages.build_mode_switch_message). Unlike every other
# hidden entry it is not filed ahead of a player's message and is not rebuilt
# each turn: it is appended once, at the moment the switch happens, and stays
# where it fell. That position is the point of it -- the system prompt at the
# top of the conversation now describes a manner the last twenty turns do not
# demonstrate, and a model reading its own earlier voice will go on imitating
# it. The note sits between the two halves and says which one is in force.
# Hidden from readers (the mode indicator on the page is what a person reads),
# stripped on the wire, never aged out by the context guard.
MODE_KIND = "mode"

# Marker on a history entry recording that a turn broke off after the server
# had already run procedures or rolls for it (issue #410; built by
# gm.messages.build_interrupted_turn_message). A turn is otherwise stored only
# when it finishes, but a procedure changes the draft the moment it runs, so a
# page that loses its connection part-way used to leave years played that the
# session had no record of. The note follows what was run and tells the model
# those results stand. Hidden from readers, stripped on the wire, never aged
# out by the context guard.
INTERRUPTED_KIND = "interrupted"

# Marker on the recap that stands in for a long session's folded turns (issue
# #579; built by gm.summary.summary_message). It is never stored: it is built
# afresh for each call from the session's saved recap and spliced in where the
# folded entries were. The marker is what tells the rest of the code that this
# entry is bookkeeping rather than something anyone said -- above all
# gm_stream, which used to read the recap's old ``system`` role as proof that
# the GM's own system prompt was already in place and so never sent it
# (issue #631).
RECAP_KIND = "recap"

# Marker on the notice that tells the player how a session fight ended (issue
# #785; built by gm.battle_report.file_fight_verdict). The reverse of the
# kinds above: shown to the reader and never to the model, which reads the
# battle's hidden closing summary instead. It is one of READER_ONLY_KINDS, so
# model_messages leaves it off every call's wire -- the turn's, the foe
# question's, the CLI's -- and the recaps (gm.summary) never read it.
FIGHT_VERDICT_KIND = "fight_verdict"

# Marker on a notice the app files into a session for its reader: a player
# withdrawing their character from a Game Master's session (issue #871). Like
# the fight's verdict, the page shows it as a notice (chat.html renders any
# entry with a ``kind`` that way) and the model never reads it, so it can
# never come back as the Game Master's own words.
SESSION_NOTICE_KIND = "session_notice"

# Every ``kind`` the reader sees and the model never does.
READER_ONLY_KINDS = frozenset({FIGHT_VERDICT_KIND, SESSION_NOTICE_KIND})


# Every ``kind`` a reader-facing view skips. A new hidden entry type adds its
# kind here so the page, the counts and the CLI transcript agree.
HIDDEN_KINDS = frozenset(
    {
        CANON_KIND,
        SHEET_KIND,
        BATTLE_KIND,
        LEDGER_KIND,
        ROLL_KIND,
        PROCEDURE_KIND,
        MODE_KIND,
        INTERRUPTED_KIND,
        RECAP_KIND,
    }
)

# Chunk size bounds, in estimated tokens. The estimate is the usual rough
# four characters per token for English prose — precise enough to keep chunks
# in the band a retrieval prompt wants, without pulling in a tokenizer.
CHARS_PER_TOKEN = 4
MIN_CHUNK_TOKENS = 300
MAX_CHUNK_TOKENS = 600

# Bumped whenever the chunker's output changes shape, so an index built by an
# older chunker is rebuilt even though the corpus bytes are unchanged.
CHUNKER_VERSION = 1
INDEX_FORMAT_VERSION = 1

# Ollama accepts a list of inputs per /api/embed call; batch to keep each
# request bounded on a 100-file corpus.
EMBED_BATCH_SIZE = 32
EMBED_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0)

# nomic-embed-text is trained with task prefixes and retrieves noticeably
# better with them; other models get the bare text.
NOMIC_DOCUMENT_PREFIX = "search_document: "
NOMIC_QUERY_PREFIX = "search_query: "

CANON_CONTEXT_PREAMBLE = (
    "Canon reference excerpts — treat these as authoritative over anything "
    "you remember:"
)

# A Markdown heading line: one to six hashes, whitespace, then the title. The
# pattern is matched against every line of the corpus, so it runs in linear
# time (issue #11). The old one trimmed the title inside the pattern with a
# lazy title between "\s+" and "\s*#*\s*", repeats that could all take the
# same spaces: cubic, 5.8 s on 2,000 spaces before a word. Here the title
# starts on a non-space (or is absent), which the whitespace before it cannot
# take, so the line divides one way; :func:`_heading` drops any closing
# hashes and the whitespace around them with ``str.rstrip``, which never
# backtracks.
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(\S.*)?$")


@dataclass(frozen=True)
class CorpusChunk:
    """One retrievable slice of the corpus.

    Attributes:
        chunk_id: Stable content hash of ``source``, ``heading`` and ``text``.
        source: Path of the originating file, relative to the corpus root.
        heading: Breadcrumb of the headings enclosing the chunk, joined by
            `` > ``; the file's stem when the chunk precedes any heading.
        text: The chunk body, headings of merged sections included inline.
    """

    chunk_id: str
    source: str
    heading: str
    text: str


@dataclass(frozen=True)
class RetrievedChunk:
    """A corpus chunk together with its cosine similarity to the query."""

    chunk: CorpusChunk
    score: float


@dataclass(frozen=True)
class GMIndex:
    """The loaded on-disk index: chunks and their unit-length vectors."""

    corpus_hash: str
    embedding_model: str
    dimensions: int
    chunks: tuple[CorpusChunk, ...]
    vectors: tuple[tuple[float, ...], ...]


@dataclass(frozen=True)
class IndexBuildResult:
    """What :func:`build_index` did, for the management command's summary."""

    path: Path
    chunk_count: int
    dimensions: int
    rebuilt: bool


Embedder = Callable[[Sequence[str]], list[list[float]]]


@dataclass
class _Section:
    breadcrumb: tuple[str, ...]
    lines: list[str]

    @property
    def text(self) -> str:
        return "\n".join(self.lines).strip()


def _estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def _heading(line: str) -> tuple[int, str] | None:
    """The level and title of a Markdown heading line, or ``None``.

    The title leaves out closing hashes and the whitespace around them:
    ``"## Title ##"`` is ``(2, "Title")``.
    """
    heading_match = HEADING_PATTERN.match(line)
    if heading_match is None:
        return None
    title = (heading_match.group(2) or "").rstrip().rstrip("#").rstrip()
    return len(heading_match.group(1)), title


def _iter_sections(markdown_text: str, fallback_title: str) -> Iterator[_Section]:
    """Yield the file's heading-delimited sections in document order."""
    breadcrumb_stack: list[tuple[int, str]] = []
    current = _Section(breadcrumb=(fallback_title,), lines=[])
    for line in markdown_text.splitlines():
        heading = _heading(line)
        if heading is None:
            current.lines.append(line)
            continue
        if current.text:
            yield current
        level, title = heading
        while breadcrumb_stack and breadcrumb_stack[-1][0] >= level:
            breadcrumb_stack.pop()
        breadcrumb_stack.append((level, title))
        current = _Section(
            breadcrumb=tuple(item_title for _level, item_title in breadcrumb_stack),
            lines=[],
        )
    if current.text:
        yield current


def _split_oversize(text: str, max_chars: int) -> list[str]:
    """Split ``text`` on paragraph boundaries into pieces of at most ``max_chars``.

    A single paragraph longer than the cap (a long table, say) is cut on line
    boundaries, and a single line longer than the cap is kept whole — the
    bound is a target for retrieval quality, not a hard protocol limit.
    """
    pieces: list[str] = []
    current: list[str] = []
    current_length = 0
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) > max_chars:
            if current:
                pieces.append("\n\n".join(current))
                current, current_length = [], 0
            pieces.extend(_split_lines(paragraph, max_chars))
            continue
        if current and current_length + len(paragraph) + 2 > max_chars:
            pieces.append("\n\n".join(current))
            current, current_length = [], 0
        current.append(paragraph)
        current_length += len(paragraph) + 2
    if current:
        pieces.append("\n\n".join(current))
    return pieces


def _split_lines(paragraph: str, max_chars: int) -> list[str]:
    pieces: list[str] = []
    current: list[str] = []
    current_length = 0
    for line in paragraph.splitlines():
        if current and current_length + len(line) + 1 > max_chars:
            pieces.append("\n".join(current))
            current, current_length = [], 0
        current.append(line)
        current_length += len(line) + 1
    if current:
        pieces.append("\n".join(current))
    return pieces


def _make_chunk(source: str, heading: str, text: str) -> CorpusChunk:
    digest = hashlib.sha256(f"{source}\n{heading}\n{text}".encode()).hexdigest()
    return CorpusChunk(chunk_id=digest[:16], source=source, heading=heading, text=text)


def chunk_markdown(markdown_text: str, *, source: str) -> list[CorpusChunk]:
    """Cut one markdown document into retrieval chunks.

    Sections are delimited by headings and carry the breadcrumb of their
    ancestors. A section over :data:`MAX_CHUNK_TOKENS` is split on paragraph
    boundaries; consecutive short sections are merged (their own heading lines
    kept inline) until the chunk reaches :data:`MIN_CHUNK_TOKENS`, so a page
    of one-paragraph subsections does not become a spray of fragments. The
    merged chunk keeps the breadcrumb of its first section.

    Args:
        markdown_text: The document's contents.
        source: Path of the document relative to the corpus root; stored on
            every chunk and folded into its id.

    Returns:
        The chunks in document order.
    """
    fallback_title = Path(source).stem
    max_chars = MAX_CHUNK_TOKENS * CHARS_PER_TOKEN
    min_chars = MIN_CHUNK_TOKENS * CHARS_PER_TOKEN

    chunks: list[CorpusChunk] = []
    pending_heading: str | None = None
    pending_parts: list[str] = []
    pending_length = 0

    def flush() -> None:
        nonlocal pending_heading, pending_parts, pending_length
        if pending_heading is not None and pending_parts:
            chunks.append(
                _make_chunk(source, pending_heading, "\n\n".join(pending_parts))
            )
        pending_heading, pending_parts, pending_length = None, [], 0

    for section in _iter_sections(markdown_text, fallback_title):
        breadcrumb = " > ".join(section.breadcrumb)
        section_text = section.text
        if len(section_text) > max_chars:
            flush()
            for piece in _split_oversize(section_text, max_chars):
                chunks.append(_make_chunk(source, breadcrumb, piece))
            continue
        # The first section of a chunk is described by the breadcrumb; later
        # merged sections keep their own heading line so the model can see
        # where one ends and the next begins.
        labelled = (
            section_text
            if not pending_parts
            else (f"{section.breadcrumb[-1]}\n{section_text}")
        )
        if pending_parts and pending_length + len(labelled) + 2 > max_chars:
            flush()
            labelled = section_text
        if not pending_parts:
            pending_heading = breadcrumb
        pending_parts.append(labelled)
        pending_length += len(labelled) + 2
        if pending_length >= min_chars:
            flush()
    flush()
    return chunks


def iter_corpus_files(root: Path) -> Iterator[tuple[str, Path]]:
    """Yield ``(source, file)`` for the corpus's markdown in sorted order.

    ``source`` is the file's path relative to ``root`` in the served layout
    (``dm-rules/...`` for the canon, wherever it lives; see
    :func:`reference.paths.iter_content_markdown`). ``proposals/`` is skipped.
    """
    for relative_path, path in iter_content_markdown(root):
        if EXCLUDED_CORPUS_DIRS.intersection(relative_path.parts[:-1]):
            continue
        yield relative_path.as_posix(), path


def iter_corpus_chunks(root: Path) -> Iterator[CorpusChunk]:
    """Walk every canon markdown file under ``root`` and yield its chunks.

    Args:
        root: The corpus directory (``reference/content`` in production).

    Yields:
        :class:`CorpusChunk` values in file order, then document order.
    """
    for source, path in iter_corpus_files(root):
        yield from chunk_markdown(path.read_text(encoding="utf-8"), source=source)


def corpus_hash(root: Path) -> str:
    """Hash the corpus contents (and the chunker version) for change detection."""
    digest = hashlib.sha256(f"chunker-v{CHUNKER_VERSION}\n".encode())
    for source, path in iter_corpus_files(root):
        digest.update(source.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _is_nomic(model: str) -> bool:
    return model.startswith("nomic-embed")


def embed_texts(
    texts: Sequence[str], *, model: str, base_url: str
) -> list[list[float]]:
    """Embed ``texts`` through Ollama's ``POST /api/embed``, batched.

    Args:
        texts: The strings to embed, already carrying any task prefix.
        model: The embedding model name as Ollama knows it.
        base_url: The Ollama origin, e.g. ``http://localhost:11434``.

    Returns:
        One vector per input, in order.

    Raises:
        httpx.HTTPError: On a transport failure or non-2xx response.
        ValueError: When the response does not carry one vector per input.
    """
    vectors: list[list[float]] = []
    endpoint = f"{base_url.rstrip('/')}/api/embed"
    with httpx.Client(timeout=EMBED_TIMEOUT) as client:
        for start in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = list(texts[start : start + EMBED_BATCH_SIZE])
            response = client.post(endpoint, json={"model": model, "input": batch})
            response.raise_for_status()
            embeddings = response.json().get("embeddings")
            if not isinstance(embeddings, list) or len(embeddings) != len(batch):
                raise ValueError(
                    f"Ollama returned {len(embeddings) if embeddings else 0} "
                    f"embeddings for {len(batch)} inputs"
                )
            vectors.extend(embeddings)
    return vectors


def embed_query(query: str, *, model: str, base_url: str) -> list[float]:
    """Embed a single search query, applying the model's query prefix."""
    prefix = NOMIC_QUERY_PREFIX if _is_nomic(model) else ""
    return embed_texts([f"{prefix}{query}"], model=model, base_url=base_url)[0]


def _document_input(chunk: CorpusChunk, *, model: str) -> str:
    prefix = NOMIC_DOCUMENT_PREFIX if _is_nomic(model) else ""
    return f"{prefix}{chunk.heading}\n\n{chunk.text}"


def _normalise(vector: Sequence[float]) -> tuple[float, ...]:
    magnitude = math.sqrt(sum(component * component for component in vector))
    if magnitude == 0.0:
        return tuple(float(component) for component in vector)
    return tuple(round(component / magnitude, 7) for component in vector)


def build_index(
    *,
    corpus_root: Path,
    index_path: Path,
    embed_model: str,
    base_url: str,
    force: bool = False,
    embedder: Embedder | None = None,
) -> IndexBuildResult:
    """Chunk and embed the corpus into ``index_path`` when it is out of date.

    The index records the corpus hash and embedding model it was built from;
    when both still match, nothing is embedded and the existing index is
    reported as-is. Vectors are stored unit-length so retrieval is a dot
    product.

    Args:
        corpus_root: Directory holding the canon markdown.
        index_path: Where the JSON index lives; parent dirs are created.
        embed_model: Embedding model recorded in, and used for, the index.
        base_url: Ollama origin for the default embedder.
        force: Rebuild even when the recorded hash and model match.
        embedder: Replacement for the Ollama call — a function from texts to
            vectors. Tests use this; production leaves it ``None``.

    Returns:
        An :class:`IndexBuildResult` describing the index on disk.

    Raises:
        FileNotFoundError: When ``corpus_root`` does not exist.
        ValueError: When the corpus yields no chunks or the embedder returns
            vectors of differing lengths.
    """
    if not corpus_root.is_dir():
        raise FileNotFoundError(f"Corpus root not found: {corpus_root}")

    current_hash = corpus_hash(corpus_root)
    if not force and index_path.exists():
        existing = load_index(index_path)
        if (
            existing.corpus_hash == current_hash
            and existing.embedding_model == embed_model
        ):
            return IndexBuildResult(
                path=index_path,
                chunk_count=len(existing.chunks),
                dimensions=existing.dimensions,
                rebuilt=False,
            )

    chunks = list(iter_corpus_chunks(corpus_root))
    if not chunks:
        raise ValueError(f"No canon markdown found under {corpus_root}")

    def ollama_embedder(texts: Sequence[str]) -> list[list[float]]:
        return embed_texts(texts, model=embed_model, base_url=base_url)

    active_embedder = embedder if embedder is not None else ollama_embedder
    raw_vectors = active_embedder(
        [_document_input(chunk, model=embed_model) for chunk in chunks]
    )
    dimensions = {len(vector) for vector in raw_vectors}
    if len(raw_vectors) != len(chunks) or len(dimensions) != 1:
        raise ValueError(
            f"Embedder returned {len(raw_vectors)} vectors of dimensions "
            f"{sorted(dimensions)} for {len(chunks)} chunks"
        )
    (dimension_count,) = dimensions

    payload = {
        "format_version": INDEX_FORMAT_VERSION,
        "corpus_hash": current_hash,
        "embedding_model": embed_model,
        "dimensions": dimension_count,
        "chunks": [
            {
                "chunk_id": chunk.chunk_id,
                "source": chunk.source,
                "heading": chunk.heading,
                "text": chunk.text,
                "vector": list(_normalise(vector)),
            }
            for chunk, vector in zip(chunks, raw_vectors, strict=True)
        ],
    }
    index_path.parent.mkdir(parents=True, exist_ok=True)
    # Write beside the index and rename over it: a live server reloads the
    # index whenever its mtime or size changes (load_index), and a deploy
    # rebuilds it while that server is serving, so it must never see a
    # half-written file.
    partial_path = index_path.with_name(index_path.name + ".partial")
    partial_path.write_text(json.dumps(payload), encoding="utf-8")
    partial_path.replace(index_path)
    return IndexBuildResult(
        path=index_path,
        chunk_count=len(chunks),
        dimensions=dimension_count,
        rebuilt=True,
    )


# Parsed indexes keyed by path, with the file signature they were read from,
# so a web worker does not re-parse several megabytes of JSON on every turn
# yet still picks up a rebuild.
_index_cache: dict[Path, tuple[tuple[int, int], GMIndex]] = {}


def load_index(index_path: Path) -> GMIndex:
    """Read the JSON index at ``index_path`` (cached by file mtime and size).

    Raises:
        FileNotFoundError: When there is no index file.
    """
    stat = index_path.stat()
    signature = (stat.st_mtime_ns, stat.st_size)
    cached = _index_cache.get(index_path)
    if cached is not None and cached[0] == signature:
        return cached[1]

    payload = json.loads(index_path.read_text(encoding="utf-8"))
    chunks = tuple(
        CorpusChunk(
            chunk_id=entry["chunk_id"],
            source=entry["source"],
            heading=entry["heading"],
            text=entry["text"],
        )
        for entry in payload["chunks"]
    )
    vectors = tuple(tuple(entry["vector"]) for entry in payload["chunks"])
    index = GMIndex(
        corpus_hash=payload["corpus_hash"],
        embedding_model=payload["embedding_model"],
        dimensions=payload["dimensions"],
        chunks=chunks,
        vectors=vectors,
    )
    _index_cache[index_path] = (signature, index)
    return index


def retrieve(
    index: GMIndex, query_vector: Sequence[float], *, k: int
) -> list[RetrievedChunk]:
    """Return the ``k`` chunks most similar to ``query_vector`` by cosine.

    Args:
        index: A loaded :class:`GMIndex` (its vectors are unit-length).
        query_vector: The embedded query; normalised here.
        k: How many chunks to return.

    Returns:
        Chunks in descending similarity order, at most ``k`` of them.

    Raises:
        ValueError: When the query vector's length differs from the index's.
    """
    if len(query_vector) != index.dimensions:
        raise ValueError(
            f"Query vector has {len(query_vector)} dimensions; "
            f"index has {index.dimensions}"
        )
    if k <= 0:
        return []
    unit_query = _normalise(query_vector)
    scored = [
        RetrievedChunk(
            chunk=chunk,
            score=sum(
                query_component * chunk_component
                for query_component, chunk_component in zip(
                    unit_query, vector, strict=True
                )
            ),
        )
        for chunk, vector in zip(index.chunks, index.vectors, strict=True)
    ]
    scored.sort(key=lambda retrieved: retrieved.score, reverse=True)
    return scored[:k]


def retrieve_for_query(
    query: str, *, index_path: Path, base_url: str, top_k: int
) -> list[RetrievedChunk]:
    """Retrieve the top-k canon chunks for a chat message, degrading visibly.

    This is the chat-time entry point, so it never raises: a missing index is
    logged as a warning and an embedding failure is logged with its traceback,
    and either way the turn proceeds with no excerpts. Every other failure
    propagates.

    Args:
        query: The player's message.
        index_path: The JSON index built by :func:`build_index`.
        base_url: Ollama origin used to embed the query with the index's model.
        top_k: How many chunks to return.

    Returns:
        The retrieved chunks, or an empty list when retrieval was not possible.
    """
    if not index_path.exists():
        logger.warning(
            "GM index not found at %s; answering without canon excerpts "
            "(run: manage.py build_gm_index)",
            index_path,
        )
        return []
    index = load_index(index_path)
    try:
        query_vector = embed_query(
            query, model=index.embedding_model, base_url=base_url
        )
        return retrieve(index, query_vector, k=top_k)
    except (httpx.HTTPError, ValueError, KeyError):
        # The ValueError branch also covers a query vector whose size does
        # not match the index (the served model changed under the same name).
        logger.exception(
            "Embedding the GM query with %s at %s failed; answering without "
            "canon excerpts",
            index.embedding_model,
            base_url,
        )
        return []


# A chat history entry as stored and sent: ``role`` and ``content`` always,
# plus ``kind`` and ``chunk_ids`` on canon entries.
ChatMessage = dict[str, Any]


def is_reader_only(message: ChatMessage) -> bool:
    """Whether ``message`` is for the reader alone (issue #785)."""
    return message.get("kind") in READER_ONLY_KINDS


def is_played_entry(message: ChatMessage) -> bool:
    """Whether ``message`` is play: something said at the table.

    Neither what the model alone reads (:data:`HIDDEN_KINDS`) nor a notice the
    app files for the reader (:data:`READER_ONLY_KINDS`: a fight's verdict, a
    player's withdrawal). The one answer to "has this session been played?",
    which a notice must never flip (third review of #871), and to what a recap
    reads (``gm.summary.for_recap``).
    """
    return not is_hidden_message(message) and not is_reader_only(message)


def is_canon_message(message: ChatMessage) -> bool:
    """Whether ``message`` is a persisted excerpt block rather than a turn."""
    return message.get("kind") == CANON_KIND


def is_battle_message(message: ChatMessage) -> bool:
    """Whether ``message`` is a persisted battle-log block (issue #345)."""
    return message.get("kind") == BATTLE_KIND


def is_roll_message(message: ChatMessage) -> bool:
    """Whether ``message`` is a server-rolled die the GM asked for (issue #363)."""
    return message.get("kind") == ROLL_KIND


def is_procedure_message(message: ChatMessage) -> bool:
    """Whether ``message`` is a rules procedure the server ran (issue #370)."""
    return message.get("kind") == PROCEDURE_KIND


def is_hidden_message(message: ChatMessage) -> bool:
    """Whether ``message`` is for the model only: any entry with a hidden ``kind``."""
    return message.get("kind") in HIDDEN_KINDS


def canon_chunk_ids(messages: Sequence[ChatMessage]) -> set[str]:
    """The ids of every chunk already shown to the model in ``messages``."""
    ids: set[str] = set()
    for message in messages:
        if is_canon_message(message):
            ids.update(message.get("chunk_ids", ()))
    return ids


# Chat templates wrap each message in a few role and separator tokens.
MESSAGE_OVERHEAD_TOKENS = 4


def estimate_message_tokens(message: ChatMessage) -> int:
    """Rough token cost of one message: its content at chars/4, plus framing."""
    return _estimate_tokens(str(message.get("content", ""))) + MESSAGE_OVERHEAD_TOKENS


def _format_excerpt(item: RetrievedChunk) -> str:
    return f"### {item.chunk.source} — {item.chunk.heading}\n{item.chunk.text}"


def select_within_budget(
    retrieved: Sequence[RetrievedChunk], *, max_tokens: int
) -> list[RetrievedChunk]:
    """Take chunks in rank order until the next would exceed ``max_tokens``.

    The first chunk is always taken, even when it alone is over budget: an
    empty block would leave the turn ungrounded for the sake of a soft bound.

    Args:
        retrieved: Chunks in descending similarity order.
        max_tokens: Budget for the rendered excerpts, in estimated tokens.

    Returns:
        A prefix of ``retrieved``.
    """
    selected: list[RetrievedChunk] = []
    used = _estimate_tokens(CANON_CONTEXT_PREAMBLE)
    for item in retrieved:
        cost = _estimate_tokens(_format_excerpt(item)) + 1
        if selected and used + cost > max_tokens:
            break
        selected.append(item)
        used += cost
    return selected


def format_canon_context(retrieved: Sequence[RetrievedChunk]) -> str:
    """Render retrieved chunks as the excerpt block the model receives."""
    sections = [_format_excerpt(item) for item in retrieved]
    return CANON_CONTEXT_PREAMBLE + "\n\n" + "\n\n".join(sections)


def canon_context_message(retrieved: Sequence[RetrievedChunk]) -> ChatMessage:
    """Build the history entry carrying the excerpts.

    It uses the ``user`` role: every chat template renders consecutive user
    messages, whereas a mid-conversation ``system`` message depends on the
    model's template honouring it. ``kind`` marks it as hidden from readers
    and ``chunk_ids`` lets later turns skip chunks the model already has.
    """
    return {
        "role": "user",
        "content": format_canon_context(retrieved),
        "kind": CANON_KIND,
        "chunk_ids": [item.chunk.chunk_id for item in retrieved],
    }


def _drop_oldest_canon(
    messages: Sequence[ChatMessage], *, budget: int
) -> tuple[list[ChatMessage], int]:
    """Remove canon entries oldest-first until the estimate fits ``budget``."""
    kept = list(messages)
    total = sum(estimate_message_tokens(message) for message in kept)
    dropped = 0
    while total > budget:
        oldest = next(
            (index for index, message in enumerate(kept) if is_canon_message(message)),
            None,
        )
        if oldest is None:
            # Nothing left that may be dropped: player and GM turns never are.
            # The list goes out over budget and what happens to it is the
            # model server's business, which is why this says so rather than
            # failing quietly (issue #579).
            logger.warning(
                "GM history is %d tokens over the %d available and nothing more "
                "may be dropped; the conversation is being sent over budget and "
                "the model server will truncate it",
                total - budget,
                budget,
            )
            break
        total -= estimate_message_tokens(kept.pop(oldest))
        dropped += 1
    return kept, dropped


def canon_entry_for_turn(
    history: Sequence[ChatMessage],
    query: str,
    *,
    index_path: Path,
    base_url: str,
    top_k: int,
    max_tokens: int,
    num_ctx: int,
) -> ChatMessage | None:
    """Retrieve the excerpt block to file in front of the player's message.

    Retrieval runs ``top_k`` wide; chunks the model has already been shown
    in this session are skipped (they are still in its context), and what
    remains is cut to ``max_tokens`` by :func:`select_within_budget`. "Already
    shown" is judged against the history as the model will see it after
    :func:`model_messages` has applied the context guard, so a chunk whose
    block has aged out can be retrieved again.

    Args:
        history: The conversation so far, ending with the player's message.
        query: The text to retrieve against (normally that message).
        index_path: The JSON index built by :func:`build_index`.
        base_url: Ollama origin.
        top_k: Retrieval width.
        max_tokens: Budget for the block, in estimated tokens.
        num_ctx: The model's context window, for the guard.

    Returns:
        The new canon entry, or ``None`` when nothing new was retrieved.
    """
    retrieved = retrieve_for_query(
        query, index_path=index_path, base_url=base_url, top_k=top_k
    )
    if not retrieved:
        return None
    visible, _dropped = _drop_oldest_canon(
        history, budget=num_ctx - REPLY_RESERVE_TOKENS
    )
    already_shown = canon_chunk_ids(visible)
    fresh = [item for item in retrieved if item.chunk.chunk_id not in already_shown]
    if not fresh:
        logger.debug(
            "All %d retrieved chunks already in the session; no new excerpts",
            len(retrieved),
        )
        return None
    return canon_context_message(select_within_budget(fresh, max_tokens=max_tokens))


def model_messages(
    history: Sequence[ChatMessage], *, num_ctx: int
) -> list[dict[str, str]]:
    """The list to send to ``/api/chat`` for ``history``.

    Every entry is reduced to ``role`` and ``content`` so the wire payload is
    the same whichever client sends it. When the estimated total would leave
    less than :data:`REPLY_RESERVE_TOKENS` of ``num_ctx`` for the reply, the
    oldest canon entries are left out first — never a player or GM turn. The
    dropped set only ever grows from one turn to the next, so the model-visible
    prefix stays stable apart from the one turn on which each block ages out.

    Args:
        history: The stored conversation, system message first.
        num_ctx: The model's context window in tokens.

    Returns:
        Plain ``{"role", "content"}`` messages in order.
    """
    # What the reader alone sees never goes on the wire (issues #785, #871).
    history = [message for message in history if not is_reader_only(message)]
    kept, dropped = _drop_oldest_canon(history, budget=num_ctx - REPLY_RESERVE_TOKENS)
    if dropped:
        logger.info(
            "GM history near the %d-token context; left out the %d oldest canon "
            "excerpt block(s) for this turn",
            num_ctx,
            dropped,
        )
    return [
        {"role": str(message["role"]), "content": str(message["content"])}
        for message in kept
    ]
