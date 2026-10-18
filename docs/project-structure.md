# Project Structure

A succinct map of every directory and significant file in **tarmar-studio**, the
unified platform for the **Tarmar** tabletop RPG: character management, an AI
Game Master and a rules reference. It runs as a Django web app, plus an offline
Ollama-powered CLI Game Master.

This file is the map of *what lives where*. For how the parts reach each other —
the deployed system, the app dependency graph, the request and auth flow, the
character pipeline, the battle lifecycle and the GM's turn engine, with diagrams
— see [architecture.md](architecture.md).

## Architecture at a glance

- **Django apps:** `accounts` (auth/users), `characters` (the Character model +
  game-rules engine), `character_export` (read-only exports), `gm` (AI Game
  Master), `reference` (markdown rules wiki), `common` (shared helpers).
- **`accounts` vs `characters`:** the custom **`User`** model lives in
  `accounts`; the **`Character`** model lives in `characters` but keeps the DB
  table name **`accounts_character`** (`Meta.db_table`) because it was migrated
  out of `accounts` without a table rename. `Character.user` → `accounts.User`.
- **Two separate Ollama integrations:** the web app (`gm/` + `common/services/
  ollama_client.py`, httpx) and the offline CLI (`cli/`), which speak to Ollama
  through that same client.
  Both send the prompt `gm.engine.system_prompt_for` chooses as the system
  message to the model named by `GM_MODEL` (default `qwen3:32b`), with
  `GM_NUM_CTX`/`GM_TEMPERATURE` as options and `think: false` — see
  `gm/model_config.py`. There is no custom model or Modelfile.
- **Rules content lives in two places:** `reference/content/` (markdown served
  by the reference site) and whatever `settings.RULES_DIR` points at (consumed by
  the GM). Both the web GM (`settings.RULES_DIR` default) and the offline CLI
  (`cli/gm.py`) default to the same subset, `reference/content/public-rules`
  (the full `reference/content/` tree, including the `dm-rules` world
  gazetteer, would overflow the model's context window) — see
  `gm.engine.DEFAULT_RULES_DIR`, the single source of truth for that path.
- **Deploy:** blue-green via systemd (`tarmar-blue` :9066 / `tarmar-green` :9067),
  each running from its own release checkout (`tarmar-studio-<colour>/`, a git
  worktree), behind an nginx upstream that `deploy.sh` flips.

## Top-level layout

| Path | Purpose |
| ---- | ------- |
| `accounts/` | Auth, users, profiles, custom `User` model |
| `characters/` | `Character` model + Tarmar rules engine (skills, dice, equipment) |
| `character_export/` | Read-only Markdown/JSON/PDF character export |
| `gm/` | AI Game Master (Ollama-backed SSE chat) |
| `reference/` | Rules reference wiki (markdown tree + search) |
| `common/` | Shared helpers, mixins, services, audit |
| `tarmar_studio/` | Django project config (settings, root urls, asgi/wsgi) |
| `cli/` | Offline Ollama CLI Game Master (`tarmar play`) |
| `static/`, `templates/` | Project-level CSS/JS/images and base templates |
| `scripts/`, `e2e/` | Deploy/coverage scripts; Playwright e2e suite |
| `nginx/`, `tarmar.service.template`, `deploy.sh` | Deploy/ops infrastructure |
| `docs/` | Ops runbooks and this structure doc |

## Django apps

### `accounts/` — identity, auth, profiles
- `models.py` — custom **`User(AbstractUser)`** (table `accounts_user`): profile fields, `is_role_admin`/`is_dm`/`is_player` flags, `can_manage_users`; phone/discord validators.
- `forms.py` — `RegistrationForm` (extends `UserCreationForm`) with profile fields.
- `views.py` — `RegisterView`, `LoginView`, `LogoutView`, `ProfileView`, `UsersListView`, `delete_user`, `profile_update_field` (inline edit).
- `urls.py` — register/login/logout, profile, users list, delete, inline update.
- `admin.py` — `UserAdmin` with Profile/Roles fieldsets.
- `templatetags/account_tags.py` — `display_roles`, `contact_choices` tags.
- `templates/accounts/` — home, about, login, register, profile, users_list (+ form-errors partial).
- `static/accounts/` — favicon, logo image, `js/preferred_contact.js`.
- `tests/` — auth, authorization, forms, models, profile, users-admin.
- `migrations/` — 3 files (0002 role flags, 0011 validators; the 0003–0010 gap is where Character used to live).

### `characters/` — Character model + rules engine
- `models.py` — **`Character`** (table `accounts_character`): rolled attributes, JSON `skills`/`equipment`/`experience_log`/`history`, aging, soft-delete, and derived properties (fatigue, body, physical/mental, effective attributes, encumbrance, armor/weapon mods).
- `views.py` — character CRUD + game actions: list/edit/deleted views, create, import, bulk-delete, mark-dead/revive, skill allocate/rename, add-experience-term, equipment endpoints.
- `urls.py` — routes for the above (`app_name="characters"`).
- `admin.py` — `CharacterAdmin`.
- `skills.py` — skills/experience engine: skill-level thresholds, `calculate_attribute_modifier`, the track readers (`track_keys`, `track_labels`, `survival_targets`), the aging lookups (keyed by aging stage — see `aging_stage_for_age`), `simulate_experience_years`. Its numbers are the loaded canon's `skill-rules.json`, read through `skill_rules.py` (issue #70).
- `skill_rules.py` — reads the canon's `skill-rules.json` into `SkillRules` (tracks, ladder, skill costs, survival roll, aging, attribute modifiers, skill and attribute checks); the cached `skill_rules()` is the one path the numbers come by, never at import (issue #70). `skill_names.py` holds `normalize_skill_name`, which both it and `skills.py` use.
- `skill_checks.py` — the skill check and the attribute check, on the canon's dice, naturals, focused-field bonus, governing attributes and Game Master's modifier limit.
- `equipment.py` — the catalog lookups the app reads (`get_item` and its siblings) and the movement restrictions a level and worn armour combine to, all from the loaded canon through `equipment_rules.py` (issue #75).
- `dice.py` — character-creation dice (d6, d100, 3d6, demon-die, fatigue), with logged variants.
- `constants.py` — shared numeric/string bounds (attribute cap 30, equipment limits, regex patterns).
- `help_text.py` — `help_text()`, the in-UI rules explanations, its rule numbers built from the loaded canon (issue #68).
- `templatetags/character_help.py` — `help_for` inline `?` disclosure tag.
- `equipment_rules.py` — reads the loaded canon's `equipment.json` into `EquipmentCatalog` (the weapons/armor/shield/gear catalog; weapon families live in `subcategory`) and its `equipment-rules.json` into `EquipmentRules` (the encumbrance levels, armour restriction codes, gaits, Missile classes and dodge bounds); the cached `equipment_catalog()` and `equipment_rules()` are the one path they come by, never at import (issue #75). `movement.py` holds the gait arithmetic on the loaded gaits.
- `templates/characters/` — character_edit, character_list, deleted_characters.
- `tests/` — 16 modules (models, views, skills, equipment, dice, import, history, validation, rules-consistency drift guard, etc.).
- `migrations/` — 7 files (0001 creates Character; later add is_played, equipment, money_earned, indexes+JSON validators, history, free_spell_points).

### `character_export/` — read-only exports (no models)
- `views.py` — `export_markdown`/`export_json`/`export_pdf` (login-required, audit-logged).
- `export_utils.py` — `generate_character_markdown/json/html` + `EXPORTABLE_FIELDS` (the round-trippable field set, shared with import).
- `urls.py` — `<id>/markdown|json|pdf`. `tests.py` — export tests.

### `gm/` — AI Game Master
- `models.py` — `GMSession`: persists a conversation as JSON history; `message_count` excludes system messages.
- `views.py` — `GMChatView` (page), `gm_stream` (SSE: streams Ollama tokens, strips `<think>`, persists on completion, handles `state`/`rules`/`roll` commands; logs failures), `gm_save`/`gm_delete`.
- `engine.py` — framework-free pieces shared with the offline CLI (`cli/gm.py`): `system_prompt()` and its three siblings, chosen by `system_prompt_for` (the full GM prompt, built on first use and cached: role, rules digest with code-rendered matrix/crit/fumble fragments, magic, equipment, a world digest of `reference/content/`, GM guidelines), `roll_dice`, `DEFAULT_RULES_DIR` (canonical `reference/content/public-rules`), `load_rules_from_dir` (reads a rules dir into one string).
- `model_config.py` — Django-free `GMModelConfig`: env names and defaults for `GM_MODEL`, `GM_NUM_CTX`, `GM_TEMPERATURE`, read by `settings.py` and by `cli/gm.py`.
- `services.py` — `get_gm_model`/`get_gm_options` (from `settings.GM_MODEL`/`GM_NUM_CTX`/`GM_TEMPERATURE`), `get_system_message`, `load_rules` (calls `engine.load_rules_from_dir` against `settings.RULES_DIR`), `roll_dice` (`NdM±K`), message builders.
- `urls.py` — chat, stream, save, delete. `admin.py` — registers `GMSession`.
- `templates/gm/chat.html` — chat UI. `tests.py` — model/services/SSE tests. `migrations/` — 1.

### `reference/` — rules reference wiki
- `nav.py` — `REFERENCE_NAV` hierarchical sidebar tree (title/slug/children, `dm_only` flags).
- `views.py` — `ReferenceView` (markdown → bleach-sanitized HTML, path-traversal-safe, DM-gated) + `search`.
- `search_index.py` — in-memory search index built at startup (rebuilds on mtime under DEBUG).
- `apps.py` — pre-warms the index in `ready()`. `urls.py` — search, index, catch-all `<path>`.
- `models.py`/`admin.py` — empty (content is filesystem markdown).
- `templates/reference/` — reference browser + search pages. `tests/` — views (permissions), search helpers.
- `content/` — the markdown rules tree (see [Rules content](#rules-content-referencecontent)).

### `common/` — shared helpers
- `helpers.py` — JSON view helpers (`_json_error`, `_can_edit_object`, `_parse_json_body`, `_require_field`).
- `number_words.py` — `number_in_words()`, small counts spelled out for prose built from the canon's rules (Django-free; issue #68).
- `mixins.py` — `AdminOrDMRequiredMixin`, `ActivePageMixin`.
- `services/ollama_client.py` — httpx Ollama client (`stream_chat`).
- `services/sse.py` — `strip_thinking`, `sse_stream` SSE wrapper.
- `decorators.py` — `rate_limit` (cache-backed, 429). `context_processors.py` — `git_info`.
- `audit.py` — the `accounts.audit` logger. `test_helpers.py` — `BaseTestCase` (gate bypass, user factories, `post_json`).

## Project config — `tarmar_studio/`
- `settings.py` — Django 5.2 settings: env-driven SECRET_KEY/DEBUG/ALLOWED_HOSTS with production fail-fast; installed apps; WhiteNoise + `speak_friend` passphrase middleware; Postgres (prod) / SQLite (dev); `AUTH_USER_MODEL="accounts.User"`; LLM block (`OLLAMA_BASE_URL`, `RULES_DIR`); prod cookie/HSTS hardening; audit logging.
- `urls.py` — root URLconf: `health/`, `/` → home, admin, apps under `/tarmar/`.
- `asgi.py`/`wsgi.py` — server entrypoints. `tests/` — gate, landing, rate-limit, settings, smoke, ui.

## CLI — `cli/` (offline Ollama Game Master)
- `tarmar.py` — argparse entrypoint: `play` (GM).
- `gm.py` — interactive local GM REPL (`ollama` + `rich`): dice rolling, session persistence, rules lookup.
- `ui.py` — terminal spinner.

## Frontend
- `templates/base.html` — root layout other templates extend. `landing.html` — public landing page. `gate.html` — passphrase gate ("Speak, friend, and enter"). `404.html`/`500.html` — themed error pages.
- `static/css/chat.css` — GM chat styling (most other styling is inlined in templates).
- `static/js/autosave.js` — debounced AJAX field auto-save. `character-management.js` — character-table AJAX (CSRF, selection, bulk delete/restore). `chat.js` — GM chat SSE client + markdown render.
- `static/js/__tests__/` — Jest unit tests for the above JS.
- `static/images/` — favicon, Tarmar logo variants, combat diagrams (facing/megahex), world/island maps.
- Per-app `templates/` — accounts (auth UI), characters (CRUD UI), gm (chat), reference (browser/search).
- `combat-movement-guide.html` (root) — standalone self-contained combat/movement rules HTML (not a Django template).

## Rules content — `reference/content/`
Markdown tree; slugs map 1:1 to `reference/nav.py`.
- Loose top pages: `get-started`, `overview`, `introduction`, `visitors-guide`.
- `explore/` — player worldbuilding (inhabitants, god-king-emperor, dragons, demons, wayfarers, pillars, ancient-ruins, magic, maja-solberg).
- `public-rules/` — player rules: `characters/` (attributes, derived-pools, injury/death, provenance, location, wealth, and `prior-experience/` with aging, survivability, skill-tracks, skills-glossary), `magic/`, `combat/` (+ `action-options/`), `equipment/`.
- `dm-rules.md` — the GM-only landing page. The pages under `dm-rules/` come from the loaded canon (`reference.paths.DM_RULES_DIR`): the private `smarks/tarmar-canon` when `TARMAR_CANON_DIR` names it, else the generic `reference/default_canon/` (issue #921).

## Deploy & ops
- `deploy.sh` — main blue-green deploy (pull + re-exec, check out origin/main into the inactive colour's release checkout, lint/types/tests, collectstatic+migrate+GM index, render and restart that colour's unit, health-check, flip nginx upstream, verify, warm GM); also `rollback`/`status`. See `DEPLOY.md` for the host layout.
- `tarmar.service.template` — the one systemd unit, rendered per colour by `deploy.sh` (`__COLOUR__`, `__PORT__`, `__RELEASE_DIR__`, `__APP_DIR__`).
- `setup-blue-green.sh` — one-time bootstrap; sources `deploy.sh` for its helpers (create both release checkouts, install units + nginx config, first deploy).
- `nginx/tarmar.conf` — site config: HTTP→HTTPS redirect, two HTTPS blocks (origamisoftware.com primary, tarmar.duckdns.org legacy), `/references/` (same-origin, no autoindex/CORS) + `/static/`, proxy to `tarmar_backend`.
- `nginx/tarmar-upstream.conf` — single-line upstream pointing at the active port; rewritten by `deploy.sh` (do not edit by hand).
- `tarmar-blue.service` / `tarmar-green.service` — gunicorn systemd units on :9066 / :9067 (localhost).
- `DEPLOY.md` — operator guide. `docs/cert-reissue-runbook.md` — how TLS certificates are issued and repointed on the deploy host (the #30/#12 reissue itself was done in #272).

## Dev tooling & tests
- `run` — task runner. uv: `dev`, `test`, `lint`, `format`, `check`, `migrate`, `update-cov`, `verify`. npm: `test:js`, `test:e2e`, `coverage:e2e`, `test:all`.
- `manage.py` — Django entrypoint.
- `scripts/health_check.py` — per-port deploy health check (`/health/`, redirects). `verify_deployment.py` — prod end-to-end verifier (gate, pages, HTML validity, error markers). `update_coverage_threshold.py` / `update_js_coverage_threshold.py` — coverage ratchets (py / js), run by hand only; hooks and CI never rewrite a threshold.
- `scripts/ci-runner/` — the self-hosted GitHub Actions runner on the Mac Studio (#576): `install-runner.sh` (run once with sudo; creates the `tarmarci` account, installs the runner and its LaunchDaemon), `protect-main.sh` (requires the CI check on `main`), the daemon plist, and a README. Held together with `ci.yml` by `scripts/test_ci_runner.py`.
- `e2e/` — Playwright suite: `global-setup.js` (seeds users + characters), `global-teardown.js`, `fixtures.js` (gate/login helpers, coverage wrap), `django.js` (Django shell helpers), `skill-rules.js` (the loaded canon's skills and experience rules, read through the shell so specs type no rule number), `equipment-rules.js` (the loaded canon's equipment catalog and equipment rules, read the same way), `character-rules.js` (the loaded canon's character creation rules and the sheet's Fatigue sum, read the same way), `coverage.js` (V8→Istanbul), specs (`auth`, `character-list`, `character-edit`).
- Config: `pyproject.toml` (deps, ruff, pytest `--cov-fail-under=98`, a fixed gate), `pyrightconfig.json`, `jest.config.js`/`jest.setup.js`, `playwright.config.js`, `package.json` (`jsCoverageThreshold`), `uv.lock`, `package-lock.json`, `.pre-commit-config.yaml` (ruff + ruff-format + pyright + makemigrations check + guides check; none rewrites a tracked file. pytest, Jest and Playwright run in CI).
- `.github/workflows/ci.yml` — runs `./run check`; `deploy.yml` — blue-green deploy over SSH, started by `./run gate` once it records a pass for the head of main.

## Docs & meta
- `README.md` — overview, prereqs, quick start. `AGENTS.md` — agent workflow (GitHub Issues, push-to-finish checklist). `CLAUDE.md` — Claude setup + `./run` commands. `.env.example` — env template. `.gitignore`.
- `.claude/settings.json` — enables the pyright-lsp plugin. `logs/audit.log` — audit log (dev placeholder). `db.sqlite3` — local dev DB (gitignored; prod is Postgres).

## Known stale / issues
Flagged for cleanup (not yet actioned):
- **`issues.sh`** — dead; bulk-creates issues in the retired `bd` (beads) tracker.
- **`TODO.md`** — predates the move to GitHub Issues; line-number references drift.
- **`auto-deploy.sh` + `tarmar_studio.service`** — pre-blue-green single-env deploy path, superseded by `deploy.sh` + the blue/green units.
- **`project-standards.md`** — contradicts current tooling (says Black/isort, conventional commits, Python 3.11) vs the actual ruff / no-conventional-commits / Python 3.13 setup.
