# Tarmar Studio

Unified Django site combining Tarmar RPG character management and GM chatbot.

## Setup

After cloning, install dependencies and pre-commit hooks:
```bash
uv sync
uv run pre-commit install   # Installs ruff/pyright/pytest hooks
```

On macOS, also `brew install glib pango` so weasyprint can render PDFs.

## Commands
```bash
./run dev         # Start development server (http://127.0.0.1:8000)
./run test        # Run all tests
./run lint        # Lint and auto-fix with ruff
./run format      # Format code with ruff
./run check       # Run all pre-commit checks
./run gate        # Every suite a deploy depends on; records the commit, then deploys it if it is main's head
./run migrate     # Run database migrations
./run update-cov  # Update coverage threshold
```

## Task Tracking (GitHub Issues)
```bash
gh issue list                          # All open issues
gh issue list --assignee @me           # Issues assigned to you
gh issue list --label bug              # Filter by label (bug, feature, comment)
gh issue view <num>                    # Show issue details
gh issue create --title "..." --body "..." --label bug --label filed-by-claude
gh issue edit <num> --add-assignee @me # Claim an issue
gh issue comment <num> --body "what I did"  # Progress notes
gh issue close <num>                   # Mark done
```

Filing an issue: use the same fields the issue forms ask for, since a session
files from the command line and the forms never apply. Label it `bug`,
`feature` or `comment` (`feature` replaces `enhancement` for new issues; the
old ones keep theirs), add `filed-by-claude` (the author GitHub shows is
Spencer's account), and a `priority: high|medium|low` label. Give the body
`### Description`, `### Severity` and `### Author` sections, the last saying
which session filed it:

```bash
gh issue create --title "..." --label bug --label filed-by-claude --label "priority: medium" \
    --body $'### Description\n...\n\n### Severity\nMajor: ...\n\n### Author\nclaude-<session id>'
```

- **Use GitHub issues for all task tracking**, not markdown TODOs or comments.
- Before starting work, run `gh issue list` (optionally filtered) to see available tasks.
- Assign yourself to claim work; comment to record progress; close when done.
- The legacy `bd` (beads) tracker and the on-disk `.beads/` directory may still appear in the repo but are no longer the source of truth — prefer GitHub issues.

## Project Structure
- `accounts/` - Authentication app (models, views, forms, templates)
- `characters/` - Character creation and management
- `character_export/` - PDF/Markdown export functionality
- `reference/` - Rules reference system (content in `reference/content/`)
- `gm/` - GM chatbot (Ollama-powered, SSE streaming, session management)
- `cli/` - Offline CLI tool (`tarmar play` — the AI Game Master)
- `common/` - Shared utilities (helpers, decorators, mixins, middleware, services)
  - `common/middleware.py` - Passphrase gate middleware
  - `common/services/ollama_client.py` - Shared Ollama HTTP client
  - `common/services/sse.py` - SSE streaming + think-tag stripping
- `tarmar_studio/` - Django project settings
- `templates/` - Shared templates (base.html, gate.html, landing.html)
- `static/` - Shared static files (css/chat.css, js/chat.js)
- `tests.py` - Core project tests

## Auth Model
- **Passphrase gate** wraps the entire site (keeps crawlers out). Exempt: `/health/`
- **User login** additionally required for all `/tarmar/` features
## Rules
- **DRY is mandatory.** Before writing new code, search for existing implementations. If similar code exists anywhere in the codebase, extract and generalize it. Never copy-paste-modify. Never write the same logic twice.
- **Do not delete or disable tests.** All existing tests must be preserved and passing.
- **All tests must pass.** Run `./run test` before committing.
- Pre-commit hooks run automatically: ruff lint, ruff format, pyright, pytest with coverage.
- **Coverage threshold auto-updates.** When coverage increases, the threshold in `pyproject.toml` automatically updates to the new value. Coverage cannot drop below the current threshold.
- Use `uv` for dependency management (`uv add <package>`).
- **Single source of truth.** Never define the same thing in two places. CI runs pre-commit hooks (not duplicated checks). Config lives in one file. If something needs to stay in sync, make one generate/call the other.
- **All editable fields should auto-save** when focus is lost.
- **A green test that missed a real bug is itself a bug.** When something breaks in play that the suite said was fine, the fix is not done until a test fails on it first. Fix the check, do not route around it and do not file the gap as acceptable: a suite that stays green through a real failure teaches everyone to stop reading it. This has cost real time here — `creation_rolls` failed 0/3 against a server that was rolling correctly, so the report was discounted for a week; the twins check went red whenever the dice killed a twin, which is the rules working; and campaign state shipped on 3,757 passing tests while doing nothing at all in play.
- **Correct beats fast, and somebody waiting is not a deadline.** Never ship a change, close an issue or call something verified because a person is standing by for it. If a fix needs another measurement run, take it and say so. A request to deploy sets the destination, not the pace.
- **The unit suite cannot test whether the model uses a capability.** Any change to the GM prompt, the plan schema or the actions is unverified until `scripts/gm_behaviour_check.py` has run against the live model with enough runs to show a rate. Passing tests are necessary and not sufficient there.

## File Hygiene
- **Never create files with spaces or numeric suffixes** (e.g., `file 2.py`, `folder 2/`). These indicate accidental duplicates - fix the root cause instead.
- **Before creating any file**, search for existing files with similar names or content.
- **Never copy directory trees.** If content exists elsewhere, import or reference it.
- **After file operations**, run `git status` to catch unintended changes before committing.
- **Content locations are fixed:**
  - Reference content: `reference/content/` only
  - Templates: `templates/` (root) for shared, `<app>/templates/<app>/` for app-specific
  - Static files: `static/` (root) for shared, `<app>/static/<app>/` for app-specific
- **Clean up after migrations.** When moving content, delete the source files.

## User Model Fields
| Field | Required | Max Length |
|-------|----------|------------|
| username | Yes | 25 |
| password | Yes | 128 |
| real_name | No | 100 |
| email | No | 254 |
| phone | No | 20 |
| discord | No | 50 |
| preferred_contact | No | - |

## Roles
Users can have any combination of roles:
| Role | Field | Default |
|------|-------|---------|
| Admin | `is_role_admin` | False |
| DM | `is_dm` | False |
| Player | `is_player` | True |

- New users automatically get the Player role upon registration
- Users can have multiple roles (e.g., Admin + DM + Player)

## Database Configuration
- **Development**: SQLite (default, no configuration needed)
- **Production**: PostgreSQL (set `DATABASE_URL` environment variable)
```bash
# Production PostgreSQL
export DATABASE_URL=postgres://user:password@host:port/dbname
uv sync --group prod  # Install psycopg
```

## Environment Variables
| Variable | Default | Purpose |
|----------|---------|---------|
| `SECRET_KEY` | dev key | Django secret key |
| `DEBUG` | `False` | Debug mode |
| `DATABASE_URL` | SQLite | PostgreSQL connection string |
| `ALLOWED_HOSTS` | localhost | Comma-separated host list |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API URL |
| `GM_MODEL` | `qwen3:32b` | Ollama model the GM (web and CLI) talks to |
| `GM_NUM_CTX` | `65536` | GM context window sent as `options.num_ctx`; the system prompt may take a quarter of it (`SYSTEM_PROMPT_CONTEXT_SHARE`, issue #907) |
| `GM_TEMPERATURE` | `0.8` | GM sampling temperature sent as `options.temperature` |
| `GM_STREAM_BUDGET_SECONDS` | `300` | Wall-clock budget for one streamed GM turn; past it the player gets a readable "took too long" message instead of a connection that never ends, and the turn keeps whatever the server already ran (issue #553) |
| `GM_RECAP_BUDGET_SECONDS` | `300` | Wall-clock budget for the recap a continued session's first turn writes before the turn's own budget starts; at it the call is hung up and the last session's fold recap is carried instead, labelled as one (issue #936) |
| `GM_FIGHT_BUDGET_SECONDS` | `50` | Wall-clock budget for `/fight`'s foe calls together (call, fallback and second ask share one deadline); a cut is a 504 in words. Below nginx's 60 s proxy_read_timeout; raise the two together (issue #936) |
| `RULES_DIR` | `<BASE_DIR>/reference/content/public-rules` | Rules markdown the GM loads |
| `GM_RETRIEVAL_ENABLED` | `true` | Ground each GM chat turn in retrieved canon excerpts (forced off under pytest) |
| `GM_EMBED_MODEL` | `nomic-embed-text` | Ollama embedding model for the GM index |
| `GM_RETRIEVAL_TOP_K` | `6` | Canon chunks retrieved per GM turn (retrieval width) |
| `GM_RETRIEVAL_MAX_TOKENS` | `900` | Cap on one turn's excerpt block, in estimated tokens (chars/4); chunks the session already saw are skipped |
| `GM_INDEX_PATH` | `<BASE_DIR>/var/gm-index.json` | Where `manage.py build_gm_index` writes the index |
| `GM_CORPUS_ROOT` | `<BASE_DIR>/reference/content` | Canon corpus the index is built from (`proposals/` excluded) |

## GM Ops
- **Warm the GM prompt cache after an Ollama restart on the Mac Studio** (`brew upgrade ollama`, LaunchAgent restart, model swap). `deploy.sh` runs `manage.py warm_gm` after every deploy, but a restart on the LLM host empties the MLX runner's restore point and the next GM session pays a ~30 s prefill until someone runs it by hand (issue #330):
  ```bash
  ssh datahorde 'cd ~/dev/tarmar-studio && export PATH=~/.local/bin:$PATH && set -a && . ./.env && set +a && uv run python manage.py warm_gm --wait 120'
  ```
  `--wait` polls `/api/version` until Ollama is up; the last line reads `warm: system prompt cached (N tokens, ~M expected)` when the cache took, and `warm: NOT cached — ...` with both numbers when it did not. A non-zero cached count is not enough: after a prompt change Ollama still reports the stale shared prefix as cached, so the count is checked against the size of the prompt the app sends today (issue #361).
- **The GM's SSE endpoint needs a non-sync gunicorn worker.** `tarmar.service.template` runs `--worker-class gthread --threads 4 --timeout 60`. Under the sync worker the arbiter's `--timeout` doubles as a hard cap on request duration, so any GM turn over 30 s had its worker killed mid-stream (issue #361). After a deploy, confirm systemd is holding the current unit:
  ```bash
  ssh datahorde 'systemctl cat tarmar-blue | grep -A6 ExecStart'   # or tarmar-green, whichever ./deploy.sh status calls active
  ```

## URL Routes
- `/` - Redirects to `/tarmar/`
- `/health/` - Health check (exempt from passphrase gate)
- `/admin/` - Django admin
- `/tarmar/` - Tarmar home page
- `/tarmar/accounts/register/` - Registration
- `/tarmar/accounts/login/` - Login
- `/tarmar/accounts/logout/` - Logout (POST only)
- `/tarmar/accounts/profile/` - Editable profile (auto-saves on blur)
- `/tarmar/accounts/profile/update-field/` - API endpoint for auto-save (POST, JSON)
- `/tarmar/accounts/users/` - User list (admin/DM only)
- `/tarmar/characters/` - Character list
- `/tarmar/characters/create/` - Create character
- `/tarmar/characters/<id>/` - Edit character
- `/tarmar/reference/` - Rules reference
- `/tarmar/export/` - Character export
- `/tarmar/gm/` - GM chatbot (requires login)
- `/tarmar/gm/stream/` - GM SSE streaming endpoint
- `/tarmar/gm/save/` - Save/rename GM session
- `/tarmar/gm/delete/` - Delete GM session
- `/tarmar/gm/characters/attach/` - Attach a character to a GM session (POST, JSON; creates the session when none is given)
- `/tarmar/gm/characters/detach/` - Detach a character from a GM session (POST, JSON)
