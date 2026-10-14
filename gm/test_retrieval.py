"""Tests for the GM's canon retrieval (issue #325).

No test here reaches Ollama: embeddings come from a bag-of-words fake, and the
one test of the real HTTP client runs it against an ``httpx.MockTransport``.
"""

import json
import re
import tempfile
import time
from collections.abc import Sequence
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import httpx
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from common.test_helpers import (
    CANON_EXPECTATIONS,
    FAKE_EMBED_VOCABULARY,
    BaseTestCase,
    fake_embedder,
    skip_unless_canon_expects,
    write_fake_corpus,
)
from gm import retrieval
from gm.models import GMSession
from gm.retrieval import (
    CANON_CONTEXT_PREAMBLE,
    CANON_KIND,
    CHARS_PER_TOKEN,
    HEADING_PATTERN,
    MAX_CHUNK_TOKENS,
    MESSAGE_OVERHEAD_TOKENS,
    MIN_CHUNK_TOKENS,
    REPLY_RESERVE_TOKENS,
    CorpusChunk,
    GMIndex,
    RetrievedChunk,
    _heading,
    build_index,
    canon_chunk_ids,
    canon_context_message,
    canon_entry_for_turn,
    chunk_markdown,
    corpus_hash,
    embed_texts,
    estimate_message_tokens,
    is_canon_message,
    iter_corpus_chunks,
    load_index,
    model_messages,
    retrieve,
    retrieve_for_query,
    select_within_budget,
)
from gm.testing import gm_replies

VOCABULARY = FAKE_EMBED_VOCABULARY
write_corpus = write_fake_corpus


class ChunkerTest(SimpleTestCase):
    """chunk_markdown and iter_corpus_chunks."""

    def test_breadcrumbs_follow_heading_nesting(self):
        text = (
            "# Top\n\nintro\n\n"
            "## Middle\n\nmiddle body\n\n"
            "### Leaf\n\nleaf body\n\n"
            "## Sibling\n\nsibling body\n"
        )
        # Force one chunk per section by shrinking the merge threshold.
        with patch.object(retrieval, "MIN_CHUNK_TOKENS", 1):
            chunks = chunk_markdown(text, source="page.md")
        self.assertEqual(
            [chunk.heading for chunk in chunks],
            ["Top", "Top > Middle", "Top > Middle > Leaf", "Top > Sibling"],
        )
        self.assertEqual(chunks[2].text, "leaf body")
        self.assertTrue(all(chunk.source == "page.md" for chunk in chunks))

    def test_short_sections_merge_with_inline_headings(self):
        text = "# Top\n\nfirst\n\n## Second\n\nsecond\n\n## Third\n\nthird\n"
        chunks = chunk_markdown(text, source="page.md")
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].heading, "Top")
        self.assertEqual(chunks[0].text, "first\n\nSecond\nsecond\n\nThird\nthird")

    def test_merging_stops_once_minimum_size_reached(self):
        filler = "word " * (MIN_CHUNK_TOKENS * CHARS_PER_TOKEN // 5)
        text = f"# A\n\n{filler}\n\n## B\n\nshort b\n\n## C\n\nshort c\n"
        chunks = chunk_markdown(text, source="page.md")
        self.assertEqual([chunk.heading for chunk in chunks], ["A", "A > B"])
        self.assertEqual(chunks[1].text, "short b\n\nC\nshort c")

    def test_oversize_section_splits_on_paragraphs_within_bound(self):
        max_chars = MAX_CHUNK_TOKENS * CHARS_PER_TOKEN
        paragraph = "lore " * 100  # ~500 chars
        text = "# Long\n\n" + "\n\n".join([paragraph.strip()] * 12)
        chunks = chunk_markdown(text, source="long.md")
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk.text), max_chars)
            self.assertEqual(chunk.heading, "Long")
            # Paragraphs are kept whole, never cut mid-sentence.
            for piece in chunk.text.split("\n\n"):
                self.assertEqual(piece, paragraph.strip())

    def test_oversize_single_paragraph_splits_on_lines(self):
        max_chars = MAX_CHUNK_TOKENS * CHARS_PER_TOKEN
        table = "\n".join(f"| row {index} | value {index} |" for index in range(400))
        chunks = chunk_markdown(f"# Table\n\n{table}\n", source="table.md")
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk.text) <= max_chars for chunk in chunks))
        self.assertEqual(
            "\n".join(chunk.text for chunk in chunks), table, "no rows lost"
        )

    def test_page_without_heading_uses_file_stem(self):
        chunks = chunk_markdown("just prose, no heading\n", source="notes/loose.md")
        self.assertEqual(chunks[0].heading, "loose")

    def test_empty_page_yields_nothing(self):
        self.assertEqual(chunk_markdown("\n\n", source="empty.md"), [])

    def test_chunk_ids_are_stable_and_content_sensitive(self):
        text = "# Top\n\nsome body\n"
        first = chunk_markdown(text, source="page.md")
        second = chunk_markdown(text, source="page.md")
        self.assertEqual(first, second)
        changed = chunk_markdown("# Top\n\nsome other body\n", source="page.md")
        self.assertNotEqual(first[0].chunk_id, changed[0].chunk_id)
        moved = chunk_markdown(text, source="elsewhere.md")
        self.assertNotEqual(first[0].chunk_id, moved[0].chunk_id)
        self.assertEqual(len(first[0].chunk_id), 16)

    def test_corpus_walk_skips_proposals_and_orders_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_corpus(root)
            chunks = list(iter_corpus_chunks(root))
        sources = [chunk.source for chunk in chunks]
        self.assertNotIn("proposals/new-idea.md", sources)
        self.assertEqual(
            sorted(set(sources)),
            ["public-rules/combat/weapons.md", "public-rules/magic.md", "the-world.md"],
        )
        self.assertEqual(sources, sorted(sources), "file order is deterministic")

    def test_real_corpus_chunks_within_bounds_and_excludes_proposals(self):
        """The shipped corpus chunks cleanly: nothing oversize, nothing from proposals."""
        chunks = list(iter_corpus_chunks(retrieval.DEFAULT_CORPUS_ROOT))
        self.assertGreater(len(chunks), 100)
        max_chars = MAX_CHUNK_TOKENS * CHARS_PER_TOKEN
        self.assertTrue(all(len(chunk.text) <= max_chars for chunk in chunks))
        self.assertFalse(any(chunk.source.startswith("proposals/") for chunk in chunks))
        self.assertEqual(len({chunk.chunk_id for chunk in chunks}), len(chunks))
        sources = {chunk.source for chunk in chunks}
        self.assertTrue(any(source.startswith("dm-rules/") for source in sources))
        self.assertTrue(any(source.startswith("public-rules/") for source in sources))


class HeadingTest(SimpleTestCase):
    """Reading a Markdown heading line (issue #11).

    The heading pattern was rewritten to run in linear time; these pin what
    it reads, so the rewrite changed how long it takes and nothing else.
    """

    def test_level_and_title(self):
        cases = {
            "# Top": (1, "Top"),
            "###### Six": (6, "Six"),
            "#\tTabbed": (1, "Tabbed"),
            "##   Spaced   ": (2, "Spaced"),
            "## Closed ##": (2, "Closed"),
            "### Closed, padded   ###   ": (3, "Closed, padded"),
            "# Issue #11 notes": (1, "Issue #11 notes"),
            "#  Two  words ": (1, "Two  words"),
            "# ": (1, ""),
            "# ##": (1, ""),
        }
        for line, expected in cases.items():
            with self.subTest(line=line):
                self.assertEqual(_heading(line), expected)

    def test_lines_that_are_not_headings(self):
        for line in ("#NoSpace", "####### Seven", " # Indented", "", "plain", "#"):
            with self.subTest(line=line):
                self.assertIsNone(_heading(line))

    def test_crlf_headings_nest(self):
        text = "# Top\r\n\r\nintro\r\n\r\n## Middle ##\r\n\r\nbody\r\n"
        with patch.object(retrieval, "MIN_CHUNK_TOKENS", 1):
            chunks = chunk_markdown(text, source="page.md")
        self.assertEqual([chunk.heading for chunk in chunks], ["Top", "Top > Middle"])

    def test_long_runs_of_spaces_are_read(self):
        # The shapes that made the old pattern slow: a long run of spaces
        # before a word, or either side of a hash.
        spaces = " " * 5000
        self.assertEqual(_heading(f"# a{spaces}x"), (1, f"a{spaces}x"))
        self.assertEqual(
            _heading(f"# a{spaces}#{spaces}x"), (1, f"a{spaces}#{spaces}x")
        )
        self.assertEqual(_heading(f"# a{spaces}#{spaces}"), (1, "a"))

    def test_the_heading_pattern_stays_fast_on_crafted_input(self):
        # The tests above check what is read; this one checks how long it
        # takes. 2,000 spaces before a word took the old pattern 5.8 s; each
        # input here is 25 times that, so a pattern that backtracks again
        # takes hours rather than a millisecond. The last two are fast for
        # the old pattern too; they hold the new one's own slow paths (the
        # closing hashes it strips, and no title at all) to the same bound.
        spaces = " " * 50_000
        crafted = {
            "spaces then a word": f"# a{spaces}x",
            "spaces either side of a hash": f"# a{spaces}#{spaces}x",
            "hashes then a word": f"# a{'#' * 50_000}x",
            "spaces then closing hashes": f"# a{spaces}{'#' * 6}{spaces}",
            "only spaces": f"#{spaces}",
        }
        readers = {"pattern": HEADING_PATTERN.match, "heading reader": _heading}
        for input_label, line in crafted.items():
            for reader_label, read in readers.items():
                with self.subTest(input=input_label, pattern=reader_label):
                    started = time.perf_counter()
                    read(line)
                    self.assertLess(time.perf_counter() - started, 1.0)


@skip_unless_canon_expects("retrieval_cases")
class TrimmedWorldLoreReachesTheModelTest(SimpleTestCase):
    """Issue #392: world detail cut from the system prompt comes back as canon.

    The prompt was trimmed to make room for the aide's first-pass rule. Each
    fact below left ``SECTION_WORLD`` because a page under
    ``reference/content`` carries it. This indexes the real corpus and asks
    for each fact the way a table would. The embedder is a bag-of-words fake
    over the query's own words, so the test proves the page is indexed and
    reachable through ``retrieve_for_query`` within the production top-k of
    4. It does not measure how ``nomic-embed-text`` ranks it.
    """

    PRODUCTION_TOP_K = 4

    # Query, the fake embedder's vocabulary, and the facts that must come
    # back. The facts are the canon's own, on no public page, so the canon
    # supplies them (tests/expectations.json, issue #921).
    CASES = tuple(
        (case["query"], tuple(case["vocabulary"]), tuple(case["facts"]))
        for case in CANON_EXPECTATIONS.get("retrieval_cases", ())
    )

    @staticmethod
    def _embedder_over(vocabulary: Sequence[str]):
        def embed(texts: Sequence[str]) -> list[list[float]]:
            vectors: list[list[float]] = []
            for text in texts:
                words = re.findall(r"[a-z]+", text.lower())
                vectors.append([float(words.count(word)) for word in vocabulary])
            return vectors

        return embed

    def test_each_trimmed_fact_is_retrieved_and_no_longer_in_the_prompt(self):
        from gm.engine import SYSTEM_PROMPT

        for query, vocabulary, facts in self.CASES:
            with self.subTest(query=query), tempfile.TemporaryDirectory() as temp:
                embedder = self._embedder_over(vocabulary)
                index_path = Path(temp) / "gm-index.json"
                build_index(
                    corpus_root=retrieval.DEFAULT_CORPUS_ROOT,
                    index_path=index_path,
                    embed_model="fake-model",
                    base_url="http://unused",
                    embedder=embedder,
                )

                def fake_embed_texts(texts, *, model, base_url, embedder=embedder):
                    return embedder(texts)

                with patch.object(retrieval, "embed_texts", fake_embed_texts):
                    results = retrieve_for_query(
                        query,
                        index_path=index_path,
                        base_url="http://unused",
                        top_k=self.PRODUCTION_TOP_K,
                    )
                retrieved_text = "\n".join(item.chunk.text for item in results)
                for fact in facts:
                    self.assertIn(fact, retrieved_text)
                    self.assertNotIn(fact, SYSTEM_PROMPT)


class EmbedClientTest(SimpleTestCase):
    """embed_texts against a mock transport."""

    def _client_factory(self, handler):
        real_client = httpx.Client  # bound before the patch replaces the name

        def factory(**kwargs):
            return real_client(transport=httpx.MockTransport(handler))

        return factory

    def test_batches_requests_and_returns_vectors_in_order(self):
        requests: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            requests.append(payload)
            self.assertEqual(request.url.path, "/api/embed")
            return httpx.Response(
                200,
                json={"embeddings": [[float(len(text))] for text in payload["input"]]},
            )

        texts = [f"text {index:02d}" for index in range(retrieval.EMBED_BATCH_SIZE + 3)]
        with patch.object(retrieval.httpx, "Client", self._client_factory(handler)):
            vectors = embed_texts(texts, model="fake-model", base_url="http://ollama/")
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0]["model"], "fake-model")
        self.assertEqual(len(requests[0]["input"]), retrieval.EMBED_BATCH_SIZE)
        self.assertEqual(len(requests[1]["input"]), 3)
        self.assertEqual(vectors, [[7.0]] * len(texts))

    def test_wrong_vector_count_is_an_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"embeddings": [[1.0]]})

        with patch.object(retrieval.httpx, "Client", self._client_factory(handler)):
            with self.assertRaisesRegex(ValueError, "1 embeddings for 2 inputs"):
                embed_texts(["a", "b"], model="m", base_url="http://ollama")

    def test_http_error_propagates(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom")

        with patch.object(retrieval.httpx, "Client", self._client_factory(handler)):
            with self.assertRaises(httpx.HTTPStatusError):
                embed_texts(["a"], model="m", base_url="http://ollama")

    def test_nomic_prefixes_applied_only_for_nomic_models(self):
        seen: list[list[str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            seen.append(payload["input"])
            return httpx.Response(
                200, json={"embeddings": [[1.0] for _ in payload["input"]]}
            )

        with patch.object(retrieval.httpx, "Client", self._client_factory(handler)):
            retrieval.embed_query(
                "who rules?", model="nomic-embed-text", base_url="http://o"
            )
            retrieval.embed_query("who rules?", model="bge-m3", base_url="http://o")
        self.assertEqual(seen, [["search_query: who rules?"], ["who rules?"]])


class IndexBuildTest(SimpleTestCase):
    """build_index idempotence and change detection."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name) / "corpus"
        self.root.mkdir()
        write_corpus(self.root)
        self.index_path = Path(self._temp.name) / "var" / "gm-index.json"

    def tearDown(self):
        self._temp.cleanup()

    def _build(self, **overrides):
        options = {
            "corpus_root": self.root,
            "index_path": self.index_path,
            "embed_model": "fake-model",
            "base_url": "http://unused",
            "embedder": fake_embedder,
        }
        options.update(overrides)
        return build_index(**options)

    def test_build_replaces_the_index_in_one_step(self):
        """The finished file appears by rename; no partial file is left behind."""
        self._build()
        sibling_names = sorted(path.name for path in self.index_path.parent.iterdir())
        self.assertEqual(sibling_names, [self.index_path.name])

    def test_builds_index_file_with_metadata_and_unit_vectors(self):
        result = self._build()
        self.assertTrue(result.rebuilt)
        self.assertEqual(result.dimensions, len(VOCABULARY))
        self.assertEqual(result.path, self.index_path)
        payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["embedding_model"], "fake-model")
        self.assertEqual(payload["corpus_hash"], corpus_hash(self.root))
        self.assertEqual(payload["dimensions"], len(VOCABULARY))
        self.assertEqual(len(payload["chunks"]), result.chunk_count)
        for entry in payload["chunks"]:
            self.assertEqual(
                set(entry), {"chunk_id", "source", "heading", "text", "vector"}
            )
            magnitude = sum(component**2 for component in entry["vector"]) ** 0.5
            self.assertAlmostEqual(magnitude, 1.0, places=5)

    def test_second_build_is_a_no_op(self):
        self._build()
        first_bytes = self.index_path.read_bytes()
        calls: list[int] = []

        def counting_embedder(texts: Sequence[str]) -> list[list[float]]:
            calls.append(len(texts))
            return fake_embedder(texts)

        result = self._build(embedder=counting_embedder)
        self.assertFalse(result.rebuilt)
        self.assertEqual(calls, [], "nothing was embedded")
        self.assertEqual(self.index_path.read_bytes(), first_bytes)

    def test_rebuilds_when_corpus_changes(self):
        self._build()
        (self.root / "public-rules" / "magic.md").write_text(
            "# Magic\n\nMana mana mana. New text.\n", encoding="utf-8"
        )
        self.assertTrue(self._build().rebuilt)

    def test_rebuilds_when_embedding_model_changes(self):
        self._build()
        self.assertTrue(self._build(embed_model="other-model").rebuilt)
        self.assertEqual(load_index(self.index_path).embedding_model, "other-model")

    def test_force_rebuilds_unchanged_corpus(self):
        self._build()
        self.assertTrue(self._build(force=True).rebuilt)

    def test_proposal_edits_do_not_change_hash(self):
        before = corpus_hash(self.root)
        (self.root / "proposals" / "new-idea.md").write_text("# Changed\n")
        self.assertEqual(corpus_hash(self.root), before)

    def test_missing_corpus_root_raises(self):
        with self.assertRaises(FileNotFoundError):
            self._build(corpus_root=self.root / "nope")

    def test_empty_corpus_raises(self):
        empty = Path(self._temp.name) / "empty"
        empty.mkdir()
        with self.assertRaisesRegex(ValueError, "No canon markdown"):
            self._build(corpus_root=empty)

    def test_inconsistent_embedder_output_raises(self):
        def ragged(texts: Sequence[str]) -> list[list[float]]:
            return [[1.0] * (index + 1) for index in range(len(texts))]

        with self.assertRaisesRegex(ValueError, "dimensions"):
            self._build(embedder=ragged)

    def test_default_embedder_calls_embed_texts_with_document_prefix(self):
        seen: dict[str, object] = {}

        def fake_embed_texts(texts, *, model, base_url):
            seen["model"], seen["base_url"] = model, base_url
            seen["first"] = texts[0]
            return fake_embedder(texts)

        with patch.object(retrieval, "embed_texts", fake_embed_texts):
            self._build(
                embedder=None, embed_model="nomic-embed-text", base_url="http://o"
            )
        self.assertEqual(seen["model"], "nomic-embed-text")
        self.assertEqual(seen["base_url"], "http://o")
        self.assertTrue(str(seen["first"]).startswith("search_document: "))

    def test_load_index_caches_until_file_changes(self):
        self._build()
        first = load_index(self.index_path)
        self.assertIs(load_index(self.index_path), first)
        self._build(force=True, embed_model="changed")
        second = load_index(self.index_path)
        self.assertIsNot(second, first)
        self.assertEqual(second.embedding_model, "changed")


def _unit(vector: Sequence[float]) -> tuple[float, ...]:
    magnitude = sum(component**2 for component in vector) ** 0.5
    return tuple(component / magnitude for component in vector)


def _make_index(entries: Sequence[tuple[str, Sequence[float]]]) -> GMIndex:
    chunks = tuple(
        CorpusChunk(chunk_id=f"id{index}", source=f"{name}.md", heading=name, text=name)
        for index, (name, _vector) in enumerate(entries)
    )
    return GMIndex(
        corpus_hash="h",
        embedding_model="fake-model",
        dimensions=len(entries[0][1]),
        chunks=chunks,
        vectors=tuple(_unit(vector) for _name, vector in entries),
    )


class RetrieveTest(SimpleTestCase):
    """Cosine ranking over a hand-built index."""

    def setUp(self):
        self.index = _make_index(
            [
                ("dragons", [1.0, 0.0, 0.0]),
                ("emperor", [0.0, 1.0, 0.0]),
                ("mixed", [1.0, 1.0, 0.0]),
                ("swords", [0.0, 0.0, 1.0]),
            ]
        )

    def test_ranks_by_cosine_similarity(self):
        results = retrieve(self.index, [2.0, 0.0, 0.0], k=3)
        self.assertEqual(
            [item.chunk.heading for item in results], ["dragons", "mixed", "emperor"]
        )
        self.assertAlmostEqual(results[0].score, 1.0, places=6)
        self.assertAlmostEqual(results[1].score, 2**-0.5, places=6)
        self.assertAlmostEqual(results[2].score, 0.0, places=6)

    def test_k_bounds_result_count(self):
        self.assertEqual(len(retrieve(self.index, [1.0, 1.0, 1.0], k=2)), 2)
        self.assertEqual(len(retrieve(self.index, [1.0, 1.0, 1.0], k=10)), 4)
        self.assertEqual(retrieve(self.index, [1.0, 1.0, 1.0], k=0), [])

    def test_dimension_mismatch_raises(self):
        with self.assertRaisesRegex(ValueError, "2 dimensions; index has 3"):
            retrieve(self.index, [1.0, 0.0], k=1)

    def test_zero_query_vector_scores_everything_zero(self):
        results = retrieve(self.index, [0.0, 0.0, 0.0], k=4)
        self.assertTrue(all(item.score == 0.0 for item in results))


class ChatTimeRetrievalTest(SimpleTestCase):
    """retrieve_for_query, its degrade paths, and the excerpt message."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name) / "corpus"
        root.mkdir()
        write_corpus(root)
        self.index_path = Path(self._temp.name) / "gm-index.json"
        build_index(
            corpus_root=root,
            index_path=self.index_path,
            embed_model="fake-model",
            base_url="http://unused",
            embedder=fake_embedder,
        )

    def tearDown(self):
        self._temp.cleanup()

    def _fake_embed_texts(self, texts, *, model, base_url):
        self.assertEqual(model, "fake-model", "query uses the index's model")
        return fake_embedder(texts)

    def test_returns_best_chunks_for_query(self):
        with patch.object(retrieval, "embed_texts", self._fake_embed_texts):
            results = retrieve_for_query(
                "tell me about the dragon",
                index_path=self.index_path,
                base_url="http://ollama",
                top_k=1,
            )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].chunk.source, "the-world.md")
        self.assertIn("dragon", results[0].chunk.text)

    def test_missing_index_warns_and_returns_nothing(self):
        missing = Path(self._temp.name) / "absent.json"
        with self.assertLogs("gm.retrieval", level="WARNING") as logs:
            results = retrieve_for_query(
                "dragon", index_path=missing, base_url="http://ollama", top_k=3
            )
        self.assertEqual(results, [])
        self.assertIn("GM index not found", logs.output[0])
        self.assertIn("build_gm_index", logs.output[0])

    def test_embed_failure_is_logged_with_traceback_and_returns_nothing(self):
        def failing(texts, *, model, base_url):
            raise httpx.ConnectError("connection refused")

        with (
            patch.object(retrieval, "embed_texts", failing),
            self.assertLogs("gm.retrieval", level="ERROR") as logs,
        ):
            results = retrieve_for_query(
                "dragon", index_path=self.index_path, base_url="http://o", top_k=3
            )
        self.assertEqual(results, [])
        self.assertIn("Embedding the GM query", logs.output[0])
        self.assertIn("Traceback", logs.output[0])
        self.assertIn("connection refused", logs.output[0])

    def test_dimension_mismatch_at_chat_time_is_logged_not_raised(self):
        def wrong_size(texts, *, model, base_url):
            return [[1.0, 2.0] for _ in texts]

        with (
            patch.object(retrieval, "embed_texts", wrong_size),
            self.assertLogs("gm.retrieval", level="ERROR"),
        ):
            results = retrieve_for_query(
                "dragon", index_path=self.index_path, base_url="http://o", top_k=3
            )
        self.assertEqual(results, [])

    def test_canon_message_format(self):
        retrieved = [
            RetrievedChunk(
                chunk=CorpusChunk(
                    chunk_id="a",
                    source="the-world.md",
                    heading="The World > Dragons",
                    text="The dragon sleeps.",
                ),
                score=0.9,
            ),
            RetrievedChunk(
                chunk=CorpusChunk(
                    chunk_id="b",
                    source="public-rules/magic.md",
                    heading="Magic",
                    text="Mana is spent.",
                ),
                score=0.5,
            ),
        ]
        message = canon_context_message(retrieved)
        self.assertEqual(message["role"], "user")
        self.assertEqual(message["kind"], CANON_KIND)
        self.assertEqual(message["chunk_ids"], ["a", "b"])
        self.assertTrue(is_canon_message(message))
        self.assertTrue(message["content"].startswith(CANON_CONTEXT_PREAMBLE))
        self.assertIn(
            "### the-world.md — The World > Dragons\nThe dragon sleeps.",
            message["content"],
        )
        self.assertIn(
            "### public-rules/magic.md — Magic\nMana is spent.", message["content"]
        )

    def _entry(self, history, query, **overrides):
        options = {
            "index_path": self.index_path,
            "base_url": "http://o",
            "top_k": 2,
            "max_tokens": 900,
            "num_ctx": 32768,
        }
        options.update(overrides)
        with patch.object(retrieval, "embed_texts", self._fake_embed_texts):
            return canon_entry_for_turn(history, query, **options)

    def test_entry_for_a_fresh_session_carries_the_best_chunks(self):
        history = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "the dragon"},
        ]
        entry = self._entry(history, "the dragon")
        assert entry is not None
        self.assertTrue(is_canon_message(entry))
        self.assertTrue(entry["content"].startswith(CANON_CONTEXT_PREAMBLE))
        self.assertIn("### the-world.md — The World", entry["content"])
        self.assertEqual(len(entry["chunk_ids"]), 2)
        self.assertEqual(len(history), 2, "the caller decides where it goes")

    def test_no_entry_when_nothing_retrieved(self):
        history = [{"role": "user", "content": "hi"}]
        with self.assertLogs("gm.retrieval", level="WARNING"):
            entry = self._entry(
                history, "hi", index_path=Path(self._temp.name) / "absent.json"
            )
        self.assertIsNone(entry)

    def test_chunks_already_in_the_session_are_not_re_injected(self):
        first = self._entry([{"role": "user", "content": "dragon"}], "dragon", top_k=1)
        assert first is not None
        history = [
            {"role": "system", "content": "sys"},
            first,
            {"role": "user", "content": "dragon"},
            {"role": "assistant", "content": "It sleeps."},
            {"role": "user", "content": "more about the dragon"},
        ]
        self.assertIsNone(
            self._entry(history, "more about the dragon", top_k=1),
            "the model already has the only chunk retrieved",
        )
        # A query that lands on a new chunk yields an entry with only that chunk.
        second = self._entry(history, "a sword", top_k=2)
        assert second is not None
        self.assertNotIn(first["chunk_ids"][0], second["chunk_ids"])
        self.assertIn("weapons.md", second["content"])

    def test_dedupe_ignores_blocks_the_context_guard_has_aged_out(self):
        first = self._entry([{"role": "user", "content": "dragon"}], "dragon", top_k=1)
        assert first is not None
        # With a window this small the guard leaves ``first`` out of what the
        # model sees, so its chunk is fair game again.
        tiny_ctx = REPLY_RESERVE_TOKENS + 8
        history = [first, {"role": "user", "content": "dragon"}]
        again = self._entry(history, "dragon", top_k=1, num_ctx=tiny_ctx)
        assert again is not None
        self.assertEqual(again["chunk_ids"], first["chunk_ids"])

    def test_canon_chunk_ids_collects_across_entries(self):
        history = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "x", "kind": CANON_KIND, "chunk_ids": ["a"]},
            {"role": "user", "content": "q"},
            {
                "role": "user",
                "content": "y",
                "kind": CANON_KIND,
                "chunk_ids": ["b", "c"],
            },
        ]
        self.assertEqual(canon_chunk_ids(history), {"a", "b", "c"})
        self.assertEqual(canon_chunk_ids([{"role": "user", "content": "q"}]), set())


def _retrieved(chunk_id: str, text: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk=CorpusChunk(chunk_id=chunk_id, source="s.md", heading="H", text=text),
        score=1.0,
    )


class ExcerptBudgetTest(SimpleTestCase):
    """select_within_budget takes chunks in rank order until the budget is spent."""

    def test_takes_a_prefix_that_fits(self):
        # Each chunk is ~100 tokens; the preamble and headings add a little.
        chunks = [_retrieved(f"c{index}", "x" * 400) for index in range(5)]
        selected = select_within_budget(chunks, max_tokens=350)
        self.assertEqual([item.chunk.chunk_id for item in selected], ["c0", "c1", "c2"])

    def test_everything_fits_under_a_generous_budget(self):
        chunks = [_retrieved(f"c{index}", "x" * 40) for index in range(3)]
        self.assertEqual(select_within_budget(chunks, max_tokens=900), chunks)

    def test_top_chunk_always_included_even_when_over_budget(self):
        chunks = [_retrieved("big", "x" * 4000), _retrieved("small", "x")]
        selected = select_within_budget(chunks, max_tokens=10)
        self.assertEqual([item.chunk.chunk_id for item in selected], ["big"])

    def test_empty_input_selects_nothing(self):
        self.assertEqual(select_within_budget([], max_tokens=900), [])


class ModelMessagesTest(SimpleTestCase):
    """model_messages strips bookkeeping keys and guards the context window."""

    def setUp(self):
        self.history = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "c1", "kind": CANON_KIND, "chunk_ids": ["a"]},
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "c2", "kind": CANON_KIND, "chunk_ids": ["b"]},
            {"role": "user", "content": "u2"},
        ]

    def test_strips_to_role_and_content_in_order(self):
        sent = model_messages(self.history, num_ctx=32768)
        self.assertEqual(
            sent,
            [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "c1"},
                {"role": "user", "content": "u1"},
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "c2"},
                {"role": "user", "content": "u2"},
            ],
        )
        self.assertTrue(all(set(message) == {"role", "content"} for message in sent))

    def test_estimate_counts_content_and_framing(self):
        self.assertEqual(
            estimate_message_tokens({"role": "user", "content": "x" * 40}),
            10 + MESSAGE_OVERHEAD_TOKENS,
        )

    def test_drops_oldest_canon_first_and_logs(self):
        total = sum(estimate_message_tokens(message) for message in self.history)
        # One token short of fitting: exactly one block has to go.
        num_ctx = REPLY_RESERVE_TOKENS + total - 1
        with self.assertLogs("gm.retrieval", level="INFO") as logs:
            sent = model_messages(self.history, num_ctx=num_ctx)
        self.assertEqual(
            [message["content"] for message in sent], ["sys", "u1", "a1", "c2", "u2"]
        )
        self.assertIn("1 oldest canon excerpt block(s)", logs.output[0])

    def test_never_drops_player_or_assistant_turns(self):
        with self.assertLogs("gm.retrieval", level="INFO"):
            sent = model_messages(self.history, num_ctx=REPLY_RESERVE_TOKENS)
        self.assertEqual(
            [message["content"] for message in sent], ["sys", "u1", "a1", "u2"]
        )

    def test_no_log_when_everything_fits(self):
        with self.assertNoLogs("gm.retrieval", level="INFO"):
            model_messages(self.history, num_ctx=32768)


class GMStreamRetrievalTest(BaseTestCase):
    """The web endpoint files excerpts into the session, hidden from readers."""

    def setUp(self):
        super().setUp()
        self.user = self.create_user()
        self.login_as(self.user)
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name) / "corpus"
        root.mkdir()
        write_corpus(root)
        self.index_path = Path(self._temp.name) / "gm-index.json"
        build_index(
            corpus_root=root,
            index_path=self.index_path,
            embed_model="fake-model",
            base_url="http://unused",
            embedder=fake_embedder,
        )

    def tearDown(self):
        self._temp.cleanup()
        super().tearDown()

    def _post(self, body: dict) -> bytes:
        response = self.client.post(
            "/tarmar/gm/stream/", json.dumps(body), content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)
        return b"".join(response.streaming_content)

    def _fake_embed_texts(self, texts, *, model, base_url):
        return fake_embedder(texts)

    def _retrieval_on(self):
        return override_settings(
            GM_RETRIEVAL_ENABLED=True, GM_INDEX_PATH=self.index_path, GM_NUM_CTX=32768
        )

    def test_ordinary_turn_sends_excerpts_and_persists_them_hidden(self):
        with (
            self._retrieval_on(),
            patch.object(retrieval, "embed_texts", self._fake_embed_texts),
            patch("gm.turn_engine.model.ollama_client.stream_chat") as mock_stream,
        ):
            mock_stream.side_effect = gm_replies(["A dragon ", "stirs."])
            content = self._post({"message": "Tell me about the dragon"})

        self.assertIn(b"stirs", content)
        sent_messages = mock_stream.call_args.args[1]
        roles = [message["role"] for message in sent_messages]
        self.assertEqual(roles, ["system", "user", "user"])
        excerpt, latest = sent_messages[1], sent_messages[2]
        self.assertTrue(excerpt["content"].startswith(CANON_CONTEXT_PREAMBLE))
        self.assertIn("### the-world.md — The World", excerpt["content"])
        self.assertEqual(set(excerpt), {"role", "content"}, "bookkeeping stripped")
        # The message itself travels in the call's last message (issue #590).
        self.assertIn("Latest message: Tell me about the dragon", latest["content"])

        session = GMSession.objects.get()
        self.assertEqual(
            [message["content"] for message in session.history],
            [excerpt["content"], "Tell me about the dragon", "A dragon stirs."],
            "the excerpt block persists ahead of the player's message",
        )
        canon_entry = session.history[0]
        self.assertEqual(canon_entry["kind"], CANON_KIND)
        self.assertTrue(canon_entry["chunk_ids"])
        self.assertEqual(session.message_count, 2, "hidden from the count")

        # Hidden from the page and the session list too.
        page = self.client.get(f"/tarmar/gm/?session={session.pk}")
        self.assertEqual(
            [message["content"] for message in page.context["history"]],
            ["Tell me about the dragon", "A dragon stirs."],
        )
        self.assertNotContains(page, CANON_CONTEXT_PREAMBLE)
        self.assertEqual(page.context["sessions"][0].message_count, 2)

    def test_canon_entry_persists_into_an_existing_session(self):
        session = GMSession.objects.create(name="Existing", user=self.user)
        session.history = [
            {"role": "user", "content": "earlier"},
            {"role": "assistant", "content": "before"},
        ]
        session.save()
        with (
            self._retrieval_on(),
            patch.object(retrieval, "embed_texts", self._fake_embed_texts),
            patch("gm.turn_engine.model.ollama_client.stream_chat") as mock_stream,
        ):
            mock_stream.side_effect = gm_replies(["ok"])
            self._post({"session_id": session.pk, "message": "the emperor?"})
        sent_messages = mock_stream.call_args.args[1]
        self.assertEqual(len(sent_messages), 5, "system, 2 prior, excerpts, player")
        self.assertTrue(sent_messages[3]["content"].startswith(CANON_CONTEXT_PREAMBLE))
        session.refresh_from_db()
        self.assertEqual(
            [(message["role"], message.get("kind")) for message in session.history],
            [
                ("user", None),
                ("assistant", None),
                ("user", CANON_KIND),
                ("user", None),
                ("assistant", None),
            ],
        )
        self.assertEqual(session.message_count, 4)

    def _run_turns(self, turns: list[str]) -> list[list[dict[str, str]]]:
        """Play ``turns`` through the view and return each call's sent messages."""
        sent: list[list[dict[str, str]]] = []
        session_id = None
        with (
            self._retrieval_on(),
            patch.object(retrieval, "embed_texts", self._fake_embed_texts),
            patch("gm.turn_engine.model.ollama_client.stream_chat") as mock_stream,
        ):
            for turn_number, text in enumerate(turns, start=1):
                mock_stream.side_effect = gm_replies([f"reply {turn_number}"])
                body: dict = {"message": text}
                if session_id is not None:
                    body["session_id"] = session_id
                content = self._post(body)
                done = json.loads(content.decode().strip().split("\n\n")[-1][6:])
                session_id = done["session_id"]
                # Copy: the view's list is the same object across the mock's calls.
                sent.append(
                    [dict(message) for message in mock_stream.call_args.args[1]]
                )
        return sent

    def test_model_visible_prefix_is_stable_across_turns(self):
        """Turn N+1's message list begins with exactly what turn N sent.

        This is the point of issue #331: Ollama's prompt cache covers the
        shared prefix, so only the new excerpts and the player's message are
        prefilled each turn.
        """
        sent = self._run_turns(["the dragon", "the emperor", "a sword", "mana?"])
        for previous, current in zip(sent, sent[1:], strict=False):
            # Everything but each call's last message is the cached prefix.
            self.assertEqual(current[: len(previous) - 1], previous[:-1])
            self.assertGreater(len(current), len(previous))
        # Each turn adds: the player's message, the reply, [new excerpt block].
        last, before = sent[-1], sent[-2]
        self.assertEqual(last[len(before) - 1]["content"], "a sword")
        self.assertEqual(last[len(before)]["content"], "reply 3")
        self.assertIn("Latest message: mana?", last[-1]["content"])

    def test_chunks_already_shown_are_not_sent_again(self):
        # Fake corpus: the-world.md is the only chunk about dragons or the
        # emperor, so the second turn retrieves nothing new.
        with override_settings(GM_RETRIEVAL_TOP_K=1):
            sent = self._run_turns(["the dragon", "the emperor", "a sword"])
        canon_per_turn = [
            sum(1 for m in messages if CANON_CONTEXT_PREAMBLE in m["content"])
            for messages in sent
        ]
        self.assertEqual(canon_per_turn, [1, 1, 2])
        session = GMSession.objects.get()
        canon_entries = [m for m in session.history if m.get("kind") == CANON_KIND]
        self.assertEqual(len(canon_entries), 2)
        self.assertNotEqual(
            canon_entries[0]["chunk_ids"], canon_entries[1]["chunk_ids"]
        )
        self.assertEqual(session.message_count, 6)

    def test_excerpt_block_is_bounded_by_the_token_budget(self):
        with override_settings(GM_RETRIEVAL_TOP_K=3, GM_RETRIEVAL_MAX_TOKENS=1):
            sent = self._run_turns(["the dragon"])
        canon = [m for m in sent[0] if CANON_CONTEXT_PREAMBLE in m["content"]]
        self.assertEqual(len(canon), 1)
        self.assertEqual(canon[0]["content"].count("### "), 1, "top chunk only")

    def test_context_guard_leaves_old_blocks_out_of_the_model_call(self):
        session = GMSession.objects.create(name="Long", user=self.user)
        session.history = [
            {"role": "user", "content": "old", "kind": CANON_KIND, "chunk_ids": ["zz"]},
            {"role": "user", "content": "earlier"},
            {"role": "assistant", "content": "before"},
        ]
        session.save()
        with (
            override_settings(
                GM_RETRIEVAL_ENABLED=True,
                GM_INDEX_PATH=self.index_path,
                GM_NUM_CTX=REPLY_RESERVE_TOKENS + 1200,
            ),
            patch.object(retrieval, "embed_texts", self._fake_embed_texts),
            patch("gm.turn_engine.model.ollama_client.stream_chat") as mock_stream,
            self.assertLogs("gm.retrieval", level="INFO") as logs,
        ):
            mock_stream.side_effect = gm_replies(["ok"])
            self._post({"session_id": session.pk, "message": "the dragon"})
        sent = mock_stream.call_args.args[1]
        self.assertNotIn("old", [message["content"] for message in sent])
        self.assertIn("earlier", [message["content"] for message in sent])
        self.assertTrue(
            any("oldest canon excerpt block" in line for line in logs.output)
        )
        session.refresh_from_db()
        self.assertEqual(session.history[0]["content"], "old", "still persisted")

    def test_commands_do_not_retrieve(self):
        with (
            override_settings(GM_RETRIEVAL_ENABLED=True, GM_INDEX_PATH=self.index_path),
            patch.object(retrieval, "embed_texts") as mock_embed,
            patch("gm.turn_engine.model.ollama_client.stream_chat") as mock_stream,
        ):
            mock_stream.side_effect = lambda *args, **kwargs: iter(["ok"])
            self._post({"message": "3d6", "command": "roll"})
            self._post({"message": "", "command": "state"})
        mock_embed.assert_not_called()
        for call in mock_stream.call_args_list:
            roles = [message["role"] for message in call.args[1]]
            self.assertEqual(roles.count("user"), 1)

    def test_disabled_setting_skips_retrieval(self):
        with (
            override_settings(
                GM_RETRIEVAL_ENABLED=False, GM_INDEX_PATH=self.index_path
            ),
            patch.object(retrieval, "embed_texts") as mock_embed,
            patch("gm.turn_engine.model.ollama_client.stream_chat") as mock_stream,
        ):
            mock_stream.side_effect = gm_replies(["ok"])
            self._post({"message": "dragon"})
        mock_embed.assert_not_called()
        sent_messages = mock_stream.call_args.args[1]
        self.assertFalse(
            any(
                CANON_CONTEXT_PREAMBLE in message["content"]
                for message in sent_messages
            )
        )

    def test_missing_index_degrades_with_warning(self):
        with (
            override_settings(
                GM_RETRIEVAL_ENABLED=True,
                GM_INDEX_PATH=Path(self._temp.name) / "absent.json",
            ),
            patch("gm.turn_engine.model.ollama_client.stream_chat") as mock_stream,
            self.assertLogs("gm.retrieval", level="WARNING") as logs,
        ):
            mock_stream.side_effect = gm_replies(["ok"])
            content = self._post({"message": "dragon"})
        self.assertIn(b"ok", content)
        self.assertIn("GM index not found", logs.output[0])
        self.assertEqual(len(mock_stream.call_args.args[1]), 2)

    def test_suite_runs_with_retrieval_forced_off(self):
        """settings.py disables retrieval under pytest so no test reaches Ollama."""
        from django.conf import settings

        self.assertFalse(settings.GM_RETRIEVAL_ENABLED)


class BuildGMIndexCommandTest(SimpleTestCase):
    """manage.py build_gm_index."""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name) / "corpus"
        self.root.mkdir()
        write_corpus(self.root)
        self.index_path = Path(self._temp.name) / "var" / "gm-index.json"

    def tearDown(self):
        self._temp.cleanup()

    def _settings(self):
        return override_settings(
            GM_CORPUS_ROOT=self.root,
            GM_INDEX_PATH=self.index_path,
            GM_EMBED_MODEL="fake-model",
            OLLAMA_BASE_URL="http://ollama",
        )

    def _fake_embed_texts(self, texts, *, model, base_url):
        self.assertEqual((model, base_url), ("fake-model", "http://ollama"))
        return fake_embedder(texts)

    def test_builds_and_reports_summary(self):
        out = StringIO()
        with (
            self._settings(),
            patch.object(retrieval, "embed_texts", self._fake_embed_texts),
        ):
            call_command("build_gm_index", stdout=out)
        self.assertTrue(self.index_path.exists())
        summary = out.getvalue()
        self.assertIn("GM index rebuilt:", summary)
        self.assertIn(f"x {len(VOCABULARY)} dims (fake-model)", summary)
        self.assertIn(str(self.index_path), summary)

    def test_second_run_is_up_to_date_and_force_rebuilds(self):
        with (
            self._settings(),
            patch.object(retrieval, "embed_texts", self._fake_embed_texts),
        ):
            call_command("build_gm_index", stdout=StringIO())
            out = StringIO()
            call_command("build_gm_index", stdout=out)
            self.assertIn("up to date", out.getvalue())
            out = StringIO()
            call_command("build_gm_index", "--force", stdout=out)
            self.assertIn("rebuilt", out.getvalue())

    def test_unreachable_ollama_is_a_command_error(self):
        def failing(texts, *, model, base_url):
            raise httpx.ConnectError("connection refused")

        with self._settings(), patch.object(retrieval, "embed_texts", failing):
            with self.assertRaisesRegex(CommandError, "GM index build failed.*refused"):
                call_command("build_gm_index", stdout=StringIO())
        self.assertFalse(self.index_path.exists())

    def test_missing_corpus_is_a_command_error(self):
        with override_settings(
            GM_CORPUS_ROOT=self.root / "absent", GM_INDEX_PATH=self.index_path
        ):
            with self.assertRaisesRegex(CommandError, "Corpus root not found"):
                call_command("build_gm_index", stdout=StringIO())
