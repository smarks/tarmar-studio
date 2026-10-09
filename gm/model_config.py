"""GM settings, read from the environment by both the web GM and the CLI.

Django's ``tarmar_studio/settings.py`` and the standalone ``cli/gm.py`` both
need the model name, context window and temperature the GM talks to Ollama
with (:class:`GMModelConfig`), and both need the canon-retrieval knobs that
ground each turn (:class:`GMRetrievalConfig`). This module is the single
source of truth for those variable names and their defaults; it must stay
free of Django imports because the CLI runs without one.

Issue #324 replaced the custom ``tarmar-gm`` Ollama model (whose Modelfile
carried these as ``PARAMETER`` lines) with plain environment settings, so
switching models is an env change rather than an ``ollama create`` step on
the LLM host. Issue #337 brought the retrieval knobs in alongside them: the
CLI had been re-reading the same five variables with the same five defaults
written out a second time.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from gm.retrieval import (
    DEFAULT_CORPUS_ROOT,
    DEFAULT_EMBED_MODEL,
    DEFAULT_INDEX_PATH,
    DEFAULT_MAX_EXCERPT_TOKENS,
    DEFAULT_TOP_K,
)

GM_MODEL_ENV = "GM_MODEL"
GM_NUM_CTX_ENV = "GM_NUM_CTX"
GM_TEMPERATURE_ENV = "GM_TEMPERATURE"

DEFAULT_GM_MODEL = "qwen3:32b"
DEFAULT_GM_NUM_CTX = 32768
DEFAULT_GM_TEMPERATURE = 0.8


@dataclass(frozen=True)
class GMModelConfig:
    """The three knobs the GM sends to Ollama on every ``/api/chat`` call."""

    model: str = DEFAULT_GM_MODEL
    num_ctx: int = DEFAULT_GM_NUM_CTX
    temperature: float = DEFAULT_GM_TEMPERATURE

    @classmethod
    def from_environ(cls, environ: Mapping[str, str] | None = None) -> "GMModelConfig":
        """Build the config from ``environ`` (default ``os.environ``).

        Unset or blank variables fall back to the defaults. A variable that is
        set but not parseable (``GM_NUM_CTX=lots``) raises ``ValueError`` naming
        the variable rather than silently using the default, so a typo in a
        deploy environment fails at boot instead of at the first GM turn.
        """
        source = os.environ if environ is None else environ
        model = source.get(GM_MODEL_ENV, "").strip() or DEFAULT_GM_MODEL
        num_ctx = _parse_env_number(source, GM_NUM_CTX_ENV, DEFAULT_GM_NUM_CTX, int)
        temperature = _parse_env_number(
            source, GM_TEMPERATURE_ENV, DEFAULT_GM_TEMPERATURE, float
        )
        return cls(model=model, num_ctx=num_ctx, temperature=temperature)

    def options(self) -> dict[str, int | float]:
        """The ``options`` object for the Ollama ``/api/chat`` payload."""
        return {"num_ctx": self.num_ctx, "temperature": self.temperature}


GM_RETRIEVAL_ENABLED_ENV = "GM_RETRIEVAL_ENABLED"
GM_EMBED_MODEL_ENV = "GM_EMBED_MODEL"
GM_RETRIEVAL_TOP_K_ENV = "GM_RETRIEVAL_TOP_K"
GM_RETRIEVAL_MAX_TOKENS_ENV = "GM_RETRIEVAL_MAX_TOKENS"
GM_INDEX_PATH_ENV = "GM_INDEX_PATH"
GM_CORPUS_ROOT_ENV = "GM_CORPUS_ROOT"


@dataclass(frozen=True)
class GMRetrievalConfig:
    """The canon-retrieval knobs that ground each GM turn (issue #325).

    Defaults come from :mod:`gm.retrieval`, which owns the retrieval
    behaviour itself; this class only says how the environment names them.

    Attributes:
        enabled: Whether a turn is grounded in retrieved canon at all. The
            web GM additionally forces this off under pytest -- that is a
            property of the test run, not of the environment, so it stays in
            ``settings.py``.
        embed_model: Ollama embedding model the index was built with.
        top_k: Canon chunks retrieved per turn.
        max_excerpt_tokens: Cap on one turn's excerpt block, in estimated
            tokens.
        index_path: Where ``manage.py build_gm_index`` writes the index.
        corpus_root: The canon tree the index is built from.
    """

    enabled: bool = True
    embed_model: str = DEFAULT_EMBED_MODEL
    top_k: int = DEFAULT_TOP_K
    max_excerpt_tokens: int = DEFAULT_MAX_EXCERPT_TOKENS
    index_path: Path = DEFAULT_INDEX_PATH
    corpus_root: Path = DEFAULT_CORPUS_ROOT

    @classmethod
    def from_environ(
        cls, environ: Mapping[str, str] | None = None
    ) -> "GMRetrievalConfig":
        """Build the config from ``environ`` (default ``os.environ``).

        Unset or blank variables fall back to the defaults; a set but
        unparseable number raises ``ValueError`` naming the variable, the
        same way :meth:`GMModelConfig.from_environ` does.
        """
        source = os.environ if environ is None else environ
        return cls(
            enabled=_parse_env_flag(source, GM_RETRIEVAL_ENABLED_ENV, True),
            embed_model=source.get(GM_EMBED_MODEL_ENV, "").strip()
            or DEFAULT_EMBED_MODEL,
            top_k=_parse_env_number(source, GM_RETRIEVAL_TOP_K_ENV, DEFAULT_TOP_K, int),
            max_excerpt_tokens=_parse_env_number(
                source,
                GM_RETRIEVAL_MAX_TOKENS_ENV,
                DEFAULT_MAX_EXCERPT_TOKENS,
                int,
            ),
            index_path=_parse_env_path(source, GM_INDEX_PATH_ENV, DEFAULT_INDEX_PATH),
            corpus_root=_parse_env_path(
                source, GM_CORPUS_ROOT_ENV, DEFAULT_CORPUS_ROOT
            ),
        )


def _parse_env_flag(
    source: Mapping[str, str], variable_name: str, default: bool
) -> bool:
    """Read a boolean variable, spelled the way ``settings.DEBUG`` reads its own.

    Anything other than a case-insensitive ``"true"`` is false; unset or
    blank falls back to ``default``.
    """
    raw_value = source.get(variable_name, "").strip()
    if not raw_value:
        return default
    return raw_value.lower() == "true"


def _parse_env_path(
    source: Mapping[str, str], variable_name: str, default: Path
) -> Path:
    """Read a filesystem path variable, falling back to ``default`` when blank."""
    raw_value = source.get(variable_name, "").strip()
    return Path(raw_value) if raw_value else default


def _parse_env_number[NumberType: (int, float)](
    source: Mapping[str, str],
    variable_name: str,
    default: NumberType,
    parser: type[NumberType],
) -> NumberType:
    raw_value = source.get(variable_name, "").strip()
    if not raw_value:
        return default
    try:
        return parser(raw_value)
    except ValueError as error:
        raise ValueError(
            f"{variable_name} must be a {parser.__name__}, got {raw_value!r}"
        ) from error
