# Tarmar Studio

> **About this repository.** This is the public Tarmar Studio repository, seeded on 2026-10-09 from a clean snapshot of the private development repository (history is deliberately not carried over). The Tarmar canon is private: it is loaded through `TARMAR_CANON_DIR` (see `DEPLOY.md`), and without it the app runs the default canon in `reference/default_canon`. The CI workflow (`.github/workflows/ci.yml`) needs a self-hosted runner, so it will not run on GitHub-hosted runners; the deploy workflow stays on the private repository, while the operational files (`DEPLOY.md`, `deploy.sh`, `nginx/`, `scripts/llm-host/`, `scripts/ci-runner/`) are kept as reference for running your own host.

Unified platform for the Tarmar tabletop RPG — character management, AI Game Master, and rules reference.

Runs as a Django web app, plus an offline CLI Game Master powered by a local LLM via Ollama.

## Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) (package manager)
- [Ollama](https://ollama.com) (local LLM runtime — required for the GM features)
- Node.js (for JS/E2E tests only)

## Quick Start

```bash
git clone <repo-url> && cd tarmar-studio
uv sync
uv run pre-commit install     # Installs ruff/pyright/pytest hooks
```

> Task tracking lives in GitHub Issues — use `gh issue list` / `gh issue create` rather than the legacy `bd` (beads) workflow.

### macOS PDF rendering prerequisite

The character-export tests use [weasyprint](https://weasyprint.org), which needs glib and pango at runtime. On macOS:

```bash
brew install glib pango
```

The `./run` wrapper and `scripts/update_coverage_threshold.py` inject `/opt/homebrew/lib` into `DYLD_FALLBACK_LIBRARY_PATH` so dyld can find the dylibs.

### Web App (Django)

```bash
./run dev         # Start development server (http://127.0.0.1:8000)
./run test        # Run all tests
./run lint        # Lint and auto-fix with ruff
./run format      # Format code with ruff
./run check       # Run all pre-commit checks
./run gate        # Every suite a deploy depends on, recording the commit
./run migrate     # Run database migrations
```

`./run gate` is the one that stands between a merge and production: it runs
ruff, `ruff format --check`, pyright, the migration and guide checks, pytest,
Jest and Playwright in that order, then pytest, Jest and Playwright a second
time against the default canon (`TARMAR_CANON_DIR` set to
`reference/default_canon`), and on a green run in both modes records the commit it
ran on as a git note under `refs/notes/gate`, pushed to origin. `deploy.sh`
fetches that ref and refuses a commit with no record unless given `--force`
(issue #576). See DEPLOY.md.

The dev server uses SQLite and sets `DEBUG=true` automatically.

### CLI Game Master

The CLI GM runs entirely offline using Ollama — no Django server or database needed.

```bash
uv sync --group cli
ollama pull qwen3:32b   # The default GM model (one-time; override with GM_MODEL)
tarmar play             # Launch the AI Game Master
```

The GM prompt lives in code (`gm/engine.py`), so there is no custom model to build. `GM_MODEL`, `GM_NUM_CTX` and `GM_TEMPERATURE` choose the model, context window and temperature for the web GM and the CLI alike.

### Checking the GM's behaviour against the live model

`./run test` mocks Ollama, on purpose — CI must never depend on a model. That leaves the model itself unchecked, which is where every GM bug in #342, #352, #353, #356, #363 and #370 lived. `scripts/gm_behaviour_check.py` is the acceptance harness for it (issue #373): real conversations through the real `/tarmar/gm/stream/` view, asserted against the rules engine and against what the server filed.

```bash
DEBUG=true GM_MODEL=qwen3.6:27b-mlx uv run python scripts/gm_behaviour_check.py
```

It runs each scenario three times and reports a flake rate rather than a pass. `--list` shows the scenarios, `--only <key>` runs some of them, `--runs N` changes the repeat count, and `--echo` prints every conversation instead of only the failing ones. With Ollama unreachable it prints SKIPPED and exits 0.

This is on demand only. pytest does not collect it, `scripts/` is outside the coverage source, and it is not part of `./run check` — nothing in CI can come to depend on a model. Its own text scanners and the oracle that reads the survival rule out of `characters/skills.py` are unit-tested in `scripts/test_gm_behaviour_check.py`, which reaches no model at all.

## Project Structure

[docs/architecture.md](docs/architecture.md) explains how the parts fit
together, from the system context down to the Games Master's turn engine, with
diagrams at every level. The tree below and
[docs/project-structure.md](docs/project-structure.md) say what lives where.

```
accounts/          Authentication (custom User model, views, forms, templates)
characters/        Character creation, management, and the rules engine
character_export/  PDF/Markdown/JSON export
cli/               Offline CLI Game Master (tarmar play)
  tarmar.py          CLI entry point
  gm.py              Interactive GM session (Ollama chat, dice, saves, rules lookup)
  ui.py              Terminal UI helpers
common/            Shared utilities (helpers, decorators, mixins, services, audit)
gm/                GM chatbot web app (Ollama-powered, SSE streaming)
reference/         Rules reference site (markdown tree + search)
tarmar_studio/     Django project settings
static/            Shared static files
templates/         Shared templates
docs/              Runbooks and the project-structure map
```

## Worldbuilding Guides (generated)

The consolidated Tarmar guides are **generated**, never hand-edited. Since
issue #921 they live with their audience:

```
build/tarmar_players_guide.md   Everything a player may read (this repository)
build/the_world_of_tarmar.md    Merged world overview (this repository)
<canon>/guides/tarmar_gms_guide.md                At-the-table GM material
<canon>/guides/tarmar_cosmology_notes_private.md  Author's deep cosmology
```

The last two are written into the Tarmar canon's own private repository,
`smarks/tarmar-canon`, and only when `TARMAR_CANON_DIR` names it; against the
default canon the build skips them with a notice.

**Single source of truth:**

- `reference/content/*.md` — the per-page markdown the Django reference site serves.
- `reference/nav.py` (`REFERENCE_NAV`) — structure, order, the `dm_only` player/GM
  boundary, and each page's `guides` membership. Children inherit a parent's
  `guides` unless they declare their own. The DM Rules section comes from the
  loaded canon's `nav.json`.
- The canon's `author/cosmology-notes.md` — the private author cosmology (not a
  served page).

**To change a guide:** edit the source page (or `reference/nav.py`), run
`uv run python scripts/build_guides.py`, and commit the rebuilt guides with the
edit (in this repository for `build/`, in the canon's for its `guides/`). The
pre-commit hook runs `scripts/build_guides.py --check` and fails the commit when
a guide is stale; it never rebuilds or stages anything itself (issue #576).

**Add a page:** create `reference/content/<path>.md`, add a `REFERENCE_NAV` entry
(with `dm_only` / `guides` as appropriate), and commit.

The build runs an **esoteric/exoteric leak check**: it fails if any deep-secret
term reaches the player-facing guides, or if the nav puts a canon page in one.
`./run gate` and the deploy verification run `python scripts/build_guides.py
--check` to guard against a stale or hand-edited guide.

> Not part of the build: the canon's `author/tarmar_canon_for_training.md` (the
> machine-readable canon for the studio LLM) and `author/tarmar_author_bible.md`
> are maintained separately in `smarks/tarmar-canon` and are intentionally left
> out of the generator.

## Running Everything Locally

### 1. Start Ollama

```bash
brew services start ollama
ollama list    # Should show your models
```

### 2. Web App

```bash
./run migrate    # First time only
./run dev        # http://127.0.0.1:8000
```

The web GM chatbot (`/tarmar/gm/`) connects to Ollama at `http://localhost:11434` by default.

### 3. CLI Game Master

```bash
tarmar play      # GM session — needs the GM model pulled in Ollama (default qwen3:32b)
```

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `SECRET_KEY` | dev key | Django secret key |
| `DEBUG` | `False` | Debug mode (`./run dev` sets this) |
| `DATABASE_URL` | SQLite | PostgreSQL connection string |
| `ALLOWED_HOSTS` | localhost | Comma-separated host list |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API URL |
| `GM_MODEL` | `qwen3:32b` | Ollama model the GM (web and CLI) talks to |
| `GM_NUM_CTX` | `32768` | GM context window sent as `options.num_ctx` |
| `GM_TEMPERATURE` | `0.8` | GM sampling temperature sent as `options.temperature` |
| `RULES_DIR` | `<BASE_DIR>/reference/content/public-rules` | Rules markdown the GM loads into its prompt |

## Dependency Groups

| Group | Install | Purpose |
|-------|---------|---------|
| (default) | `uv sync` | Django web app |
| `dev` | `uv sync --group dev` | Linting, testing, type checking |
| `cli` | `uv sync --group cli` | Offline CLI Game Master (rich) |
| `prod` | `uv sync --group prod` | Production server (gunicorn, psycopg) |

## Production Deployment

See [DEPLOY.md](DEPLOY.md) for the blue-green deployment guide.

```bash
./deploy.sh          # Deploy latest changes
./deploy.sh rollback # Instant rollback to previous environment
./deploy.sh status   # Show active environment
```
