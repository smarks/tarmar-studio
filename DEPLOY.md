# Deployment Guide

## Blue-Green Deployment

This project uses blue-green deployment for zero-downtime updates with instant rollback.

### Layout on the host

| Path | What it is |
|------|------------|
| `/home/sam/dev/tarmar-studio` | The main checkout: `deploy.sh`, `.env`, `.deploy-state`, and the runtime state both colours share (`staticfiles/`, served by nginx; `logs/`; `var/` with the GM index) |
| `/home/sam/dev/tarmar-studio-blue` | Blue's release checkout, a git worktree of the main checkout |
| `/home/sam/dev/tarmar-studio-green` | Green's release checkout |
| `/home/sam/dev/tarmar-canon` | The Tarmar canon: a clone of the private `smarks/tarmar-canon`, whose root is the canon directory. Both colours read it through `TARMAR_CANON_DIR` |

Each systemd unit runs from its own release checkout (rendered from
`tarmar.service.template`), so pulling the main checkout never changes the
source a live worker imports from, and a deploy that fails leaves the live
colour's files untouched (issue #328). The shared directories are symlinks
from each release checkout back to the main checkout.

### Environment (`.env`)

`.env` in the main checkout is the only source of production settings,
`DATABASE_URL` included. The units read it through systemd's `EnvironmentFile`,
and `deploy.sh` reads the same file with `load_env_file`, so `migrate` always
targets the database the site serves from (issue #574). Nothing in the
repository holds a database URL.

Both readers agree only on plain lines, so `deploy.sh` accepts only blank lines,
`#` comments and `KEY=value` with no quotes, backslashes, `$`, backticks or
spaces in the value. Any other line fails the deploy, naming the line and key
but never printing the value.

### The Tarmar canon (`TARMAR_CANON_DIR`)

The DM-only canon (the pages served under `/reference/dm-rules/`, the GM
panel's maps, the dice tables and hooks the ruins generator parses, and the
world section of the Games Master's prompt) is not in this repository. It
lives in the private repository `smarks/tarmar-canon`, whose root is the canon
directory (issue #921); its README names the layout.

Production **must** set, in `.env`:

```
TARMAR_CANON_DIR=/home/sam/dev/tarmar-canon
```

Without it the app falls back to the generic default canon in
`reference/default_canon` and keeps running: the reference pages and the GM
chat page say "World: default canon", the Tarmar pages, maps and world prompt
are absent, and the only other sign is a warning in the units' log
(`TARMAR_CANON_DIR is not set, so this production process ... loaded the
generic default canon`), which `tarmar_studio.settings` logs whenever `DEBUG`
is off outside a test run. `reference.paths` reads the variable when it is
first imported, before settings loads `.env`, so it has to reach the process
as a real environment variable; systemd's `EnvironmentFile` does that for the
units, and `deploy.sh`'s `load_env_file` does it for the management commands.

`deploy.sh` keeps the checkout current. After pulling the main checkout and
re-running itself, and before it decides whether there is anything to deploy,
it clones `git@github.com:smarks/tarmar-canon.git` into
`/home/sam/dev/tarmar-canon` if the directory is absent, or fetches and
fast-forwards it (`git pull --ff-only`) if it is a checkout, and logs the
canon commit it now holds. A failed clone or fetch, a checkout that cannot
fast-forward (local commits or edits on the host), or a path that is not a
git checkout stops the deploy before anything is built. The host
authenticates to GitHub as `smarks` over ssh, which is what lets it read the
private repository.

A canon change is a change to deploy. A successful deploy records the canon
commit it put live in `.deploy-canon` in the main checkout; when the canon
checkout is at any other commit, the deploy goes ahead even if this
repository has no new commit (`--force` still deploys regardless). So a
canon change ships by pushing it to `smarks/tarmar-canon` and running
`./deploy.sh`, and a deploy that pulled a canon change but failed leaves the
next run still deploying it.

Before it builds a release, the deploy reads `TARMAR_CANON_DIR` from `.env`
(in a subshell, so nothing else in `.env` reaches the test step) and refuses
to go on unless it names exactly that checkout, trailing slashes aside, so
production can never serve a canon the deploy did not pull.
`TARMAR_CANON_REMOTE` and `TARMAR_CANON_CHECKOUT` override the two locations;
the tests use them, and nothing on the host should.

The gate record has to vouch for the Tarmar canon too. `deploy.sh` (unless
`--force`) and `./run gate --check` refuse a record whose first pass ran the
default canon, which is what a gate run without `TARMAR_CANON_DIR` records.
The record's `canon-commit:` line names the canon commit the suites ran
against; when the canon the deploy pulls is at another commit, the deploy
says so in a warning and carries on, since that canon change has not been
through the suites.

Three things follow from both colours reading one checkout. The canon's pages
are read per request, so the live colour serves canon pages from the moment
the deploy pulls them, while its nav, maps and prompt (read at import) change
only when it restarts. `deploy.sh rollback` switches the code back but not the
canon. And the GM retrieval index is rebuilt by `build_gm_index` when the
corpus hash changes, which a canon change alone does.

Before pushing a canon change, regenerate its guides from a studio checkout
with `TARMAR_CANON_DIR` pointing at it: `uv run python scripts/build_guides.py`
writes the Game Master's guide and the private cosmology notes into the
canon's `guides/`, and `--check` verifies them (and runs the player guides'
leak check against the canon's `author/forbidden-terms.txt`).

### Deploy lock

`deploy.sh` and `deploy.sh rollback` take an exclusive `flock` on `.deploy.lock`
in the main checkout and fail at once with "deploy in progress" if another run
holds it. `deploy.sh status` does not lock.

The lock lives on the open file, and descriptor 9 is inherited when the script
re-executes itself from the freshly pulled copy, so that copy keeps the lock it
already holds instead of re-opening the file (re-opening drops the lock for an
instant, which is long enough for a rival deploy to take it and leave the
pulled copy failing its own `flock`). `TARMAR_DEPLOY_LOCKED` travels with the
descriptor; if the descriptor somehow does not arrive, the pulled copy takes
the lock afresh rather than deploying without one.

### The gate, and what "green" means

CI runs on the Mac Studio's self-hosted runner (`scripts/ci-runner/`, issue
#576); the commit hook no longer runs pytest or Playwright, so a commit that
skipped `./run gate` and never went through a pull request has had no suite run
on it at all. `./run gate` is the check that stands between a merge and a
deploy whatever CI says, and the only one while the runner is not registered.

```bash
./run gate                 # run everything, record the commit, push the record
./run gate --check         # does HEAD already have a record?
./run gate --check <sha>   # ...or that commit
./run gate --no-push       # record locally only (a deploy host will not see it)
```

It runs, in order: `ruff check`, `ruff format --check`, `pyright`,
`makemigrations --check --dry-run`, `build_guides.py --check`, `pytest -q`,
`npx jest`, and Playwright through `scripts/update_js_coverage_threshold.py
--check` on its own `E2E_PORT` (8390, so a dev server on 8000 neither answers
the suite's requests nor loses its own). It then runs pytest, Jest and
Playwright a second time, named `pytest (default canon)` and so on, with
`TARMAR_CANON_DIR` set to the absolute path of `reference/default_canon` and
Playwright on `E2E_PORT` 8391 (`--default-canon-e2e-port`). That second pass
proves the configuration of a checkout without the private canon (issue
#920). Ruff, pyright and the migration check do not read the canon and run
once. So does `build_guides.py --check`, in the first pass: `build/` holds only
the player-facing guides, the same under any canon, and when the first pass's
canon is the Tarmar canon the check covers that canon's `guides/` too. The first
pass uses the canon your own `TARMAR_CANON_DIR` names, passed through unchanged
and recorded in the note; the second pass always sets the default canon's path.
Since #921 the checkout holds no canon, so run the gate with `TARMAR_CANON_DIR`
set to a clone of `smarks/tarmar-canon`: without it both passes run the default
canon, the Tarmar canon goes untested, and the note's `canon:` line says
`default`, which `deploy.sh` and `./run gate --check` refuse. The note also
records `canon-commit:`, the commit of the canon checkout the first pass ran
(`none` for a canon that is not a checkout of its own), and the gate refuses
to start, or to record, while that checkout has uncommitted changes.
There is no flag to skip it. The first failure stops the run, and the gate
refuses to start if `reference/default_canon` does not exist.

`jsCoverageThreshold` in `package.json` must clear both passes. The ratchet
prints in `--check` mode and never rewrites it, so the first pass cannot raise
the bar for the second; but a hand-raise based on a Tarmar-canon run alone could
set one the default canon cannot meet.

When every step passes it records the commit as a git note under
`refs/notes/gate` and pushes the ref to origin:

```
gate: pass
commit: <full sha>
recorded: 2026-09-21T17:04:05Z
host: <machine that ran the suites>
canon: tarmar
canon-dir: <absolute path of the canon the first pass ran against>
canon-commit: <the canon checkout's commit, or none>
e2e-port: 8390
default-canon-e2e-port: 8391
steps: ruff, ruff format, pyright, makemigrations, build_guides, pytest, jest, playwright, pytest (default canon), jest (default canon), playwright (default canon)
```

A note means every step passed **on that exact tree**, so the gate refuses to
run at all with a dirty working tree, and refuses to record if HEAD moved or
the tree changed while the suites ran (they take long enough for that to
happen).

`deploy.sh` fetches `refs/notes/gate` from origin — a plain `git fetch` does
not carry `refs/notes/*` — and refuses an `origin/main` commit with no note
before it pulls or builds anything, naming the commit and how to record one.
`./deploy.sh --force` deploys anyway and says so. `rollback`, `status` and
`backup` are unaffected: a rollback goes to a colour that was already deployed.

The record lives in git rather than in a file under the app directory because
the deploy host must not trust its own working tree: a stale or hand-written
file would wave a deploy through, while a note is keyed by commit and comes
from origin. One ref holds every record, which at roughly fifty merges a day is
also why it is not a tag per commit.

### First-Time Setup

Run as `sam` (the script uses `sudo` for the root-owned pieces, as `deploy.sh` does):

```bash
./setup-blue-green.sh
```

### Deploy

```bash
./deploy.sh
```

This will:
1. Refuse the commit outright if `refs/notes/gate` holds no passing record for it (unless `--force`)
2. Pull latest code into the main checkout, then re-run itself from the pulled copy (bash reads the script before the pull replaces it)
3. Check out origin/main into the inactive colour's release checkout
4. Run linting, type checks, and tests there
5. Sync dependencies, check no model change lacks a migration, collect static files, back up the database, run migrations, build the GM index
6. Render and install the colour's systemd unit and restart it
7. Run exhaustive health checks against it
8. Switch traffic to the new environment
9. Keep the old environment running, on its own checkout, for rollback

### Commands

| Command | Description |
|---------|-------------|
| `./deploy.sh` | Deploy latest changes |
| `./deploy.sh --force` | Redeploy even if no changes, and without a passing gate record |
| `./deploy.sh rollback` | Switch back to previous environment |
| `./deploy.sh status` | Show which environment is active and what revision each colour runs |
| `./deploy.sh backup` | `pg_dump -Fc` the database into `var/backups/` (what the nightly timer runs) |

### Migrations must not break the old colour

`migrate` runs on the shared database while the old colour is still serving,
and a rollback runs old code against the new schema. So every migration must
**expand** the schema in a way the previous release tolerates (issue #575):

- New columns are `null=True` or have `db_default=`. A plain `default=` is not
  enough: Django fills existing rows, then drops the default from the database,
  and the old code's inserts fail.
- Removing, renaming or re-tabling a column or model ships one release *after*
  the code stops reading it.
- `RunPython` always has a `reverse_code` (`migrations.RunPython.noop` if there
  is nothing to undo).

`tarmar_studio/tests/test_migration_safety.py` enforces this on every migration
added after the baseline in `tarmar_studio/migration_baseline.json`. A deliberate
contract step goes in that file's `allowed` map, `"app.migration": "reason"`.
`makemigrations --check --dry-run` runs in the commit hook and in `deploy.sh`,
where a model change with no migration fails the deploy.

### Backups

Right before `migrate`, `deploy.sh` runs `pg_dump -Fc` into
`var/backups/deploy-<UTC timestamp>.dump` in the main checkout, keeping the
newest 10. A failed dump stops the deploy before anything is migrated.
`tarmar-backup.timer` (installed and enabled by every deploy, from
`tarmar-backup.service.template` and `tarmar-backup.timer`) takes a
`nightly-<timestamp>.dump` around 03:30, also keeping 10. The two kinds are
pruned separately.

Restore one with:

```bash
pg_restore --clean --if-exists --no-owner --dbname="$DATABASE_URL" var/backups/<file>.dump
```

Check the timer with `systemctl list-timers tarmar-backup.timer`.

### Health Checks

Before switching traffic, the deploy verifies:
- Home page loads with correct content
- Static files are served
- Login page renders with CSRF token
- Database connectivity (form POST works)
- Register page loads
- 404 handling works (not 500)

### Manual Verification

```bash
./run verify  # Check production site
```

## The Games Master LLM host

The GM model does not run on this host. It runs under Ollama on a Mac Studio on
the same LAN, reached through `OLLAMA_BASE_URL` in `.env`, which names
`tarmar-llm` rather than an address — the Mac publishes its own DHCP address
into this host's `/etc/hosts` under that name.

`deploy.sh` warms the GM prompt cache at the end of every deploy. The other
half, warming after Ollama itself restarts, is a LaunchAgent on the Mac:
`scripts/llm-host/`, with its own README covering what it does, how to install
it and what it deliberately does not cover.

### Rollback

If something goes wrong after deploy:

```bash
./deploy.sh rollback
```

Traffic instantly switches to the previous environment, unless the database
already has migrations the previous environment's revision does not have. Then
the rollback refuses and lists them (`scripts/rollback_migrations.py`), because
that code would run against a schema it was not written for; deploy a fix
forward instead.
