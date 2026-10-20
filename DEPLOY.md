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
repository holds a database URL. The one setting kept apart is the nightly
restore check's connection, in `.env.restore-check` beside it, which the units
never load (see [Backups](#backups)). Both files are mode 600.

Both readers agree only on plain lines, so `deploy.sh` accepts only blank lines,
`#` comments and `KEY=value` with no quotes, backslashes, `$`, backticks or
spaces in the value. Any other line fails the deploy, naming the line and key
but never printing the value.

### Mail

Password reset (issue #929) sends its link by email, and so does email-address
confirmation (issue #931); nothing else on the site sends mail. Production
hands every message to the host's own postfix on `localhost:25`, with no login
and no TLS, which is what the settings default to. The one value `.env` must add is the sender:

```
DEFAULT_FROM_EMAIL=tarmar@roots.origamisoftware.com
```

With `DEBUG=False` the settings refuse to start without it, so a deploy that
lacks it stops at `collectstatic` and the live colour keeps serving. Use an
address postfix will accept as a sender, and a bare address: `load_env_file`
refuses spaces, quotes, backslashes, `$` and backticks in a value, so the
space in a `Name <address>` form fails the deploy. Whether the
receiving server takes the message is down to that domain's SPF and DMARC
records, so test it from the main checkout before relying on it:

```bash
set -a; . ./.env; set +a
uv run python manage.py sendtestemail you@example.com
```

A send that fails during a reset is logged to the journal (`django.contrib.auth`,
"Failed to send password reset email"), and the visitor still sees the same
"sent if the account exists" page, so the journal is where to look.

| Variable | Default | Set it when |
|----------|---------|-------------|
| `DEFAULT_FROM_EMAIL` | none in production (`tarmar@localhost` under DEBUG) | always, in production |
| `EMAIL_BACKEND` | console under DEBUG, SMTP otherwise | never on the host |
| `EMAIL_HOST` / `EMAIL_PORT` | `localhost` / `25` | relaying somewhere other than the local postfix |
| `EMAIL_HOST_USER` / `EMAIL_HOST_PASSWORD` | empty | that relay needs a login |
| `EMAIL_USE_TLS` | `false` | that relay needs STARTTLS |
| `SITE_BASE_URL` | unset: the email follows the request, the command uses `https://` + the first `ALLOWED_HOSTS` entry | you want every reset link on one fixed name, independent of the Host header |

An account with no email address on file, or a site whose mail is not working
yet, gets its link from the host operator instead. It is the same signed link
the email would carry, it works once, it expires after three days, and whoever
holds it can set the password, so hand it over the way you would a password.
From the main checkout:

```bash
set -a; . ./.env; set +a
uv run python manage.py password_reset_link <username>
uv run python manage.py password_reset_link <username> --base-url https://tarmar.origamisoftware.com
```

`manage.py changepassword <username>` still works too, when the operator is
the one choosing the password.

#### Confirmed addresses (issue #931)

The reset form mails only an address that has been **confirmed**: someone
opened a link sent to it. Changing an address on the profile takes the
account's password, sends a confirmation link to the new address and a notice
to the old one, and the address on file changes only when the link is opened
(it works once, expires with `PASSWORD_RESET_TIMEOUT`, and dies if a newer
change is asked for or the password changes). An address typed at
registration is unconfirmed until its link is opened. Admins and DMs cannot
change another account's address. Migration `accounts.0012` marked every
address already on file as confirmed, since each was a working reset route
until then.

When the operator doubts an address, or has confirmed one some other way:

```bash
uv run python manage.py set_email_verified <username> false
uv run python manage.py set_email_verified <username> true
```

`false` stops the reset form mailing that address until its holder confirms it
again from their profile (Change email address, the same address). Both lines
are written to the audit log. Django's admin shows the same flag under "Email
confirmation"; an address changed there is not confirmed by being typed, so
saving a changed address unticks the flag unless the same save changes the
flag on purpose. A confirmation link that
fails to send is logged to the journal (`accounts.email_change`), and the
change form shows again saying nothing changed. The notice to the old address
goes only to a confirmed address, within the same three-an-hour allowance per
inbox as everything else that mails.

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
it finds the canon commit it would serve without moving the checkout: if the
checkout exists it fetches into it and reads its upstream (`@{u}`), and if the
directory is absent it reads the HEAD of `git@github.com:smarks/tarmar-canon.git`
with `git ls-remote`. It logs that commit. Only once the deploy has decided to
go ahead and the gate record has vouched for that commit (below; under
`--force` no record is read and nothing vouches for it) does it move the
checkout, to exactly that commit and nothing newer: `git merge --ff-only` on
the commit it read, or `git clone --no-checkout` into
`/home/sam/dev/tarmar-canon` followed by `git reset --hard` to it, so no
other tree is ever written there. A failed fetch, `ls-remote` or clone, a
checkout with no upstream, a fast-forward that fails (uncommitted edits on the
host to files it changes; edits to other files are carried along), or a path
that is not a git checkout stops the deploy before anything is built. Commits
made on the host are covered below. The host authenticates to GitHub as `smarks` over
ssh, which is what lets it read the private repository.

A canon change is a change to deploy. A successful deploy records the canon
commit it put live in `.deploy-canon` in the main checkout; when the checkout
is at any other commit or absent, or origin's canon has moved past it, the
deploy goes ahead even if this repository has no new commit (`--force` still
deploys regardless). So a canon change ships by pushing it to
`smarks/tarmar-canon` and running `./run ship`, which gates `main`'s head
against the new canon and starts the deploy, and a deploy that moved the
checkout to a canon change but failed leaves the next run still deploying it.

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
against, and a record without one is refused. When the canon the deploy would
serve is at another commit, the deploy stops before anything is built, naming
both commits, since that canon change has not been through the suites and the
canon holds rules the code loads (`magic-rules.json` for battles,
`character-rules.json` for character creation, `skill-rules.json` for skills,
prior experience and checks, `equipment.json` for the equipment catalog and
`equipment-rules.json` for encumbrance, movement, Archery and the attack
panel's dodge), not only lore.
The check runs before the checkout moves, against the commit it would move to,
so a refused canon never reaches the checkout the live colour reads. Run
`./run ship` again: it gates `main`'s head against the canon's head and
deploys. A record of `canon-commit: none` is refused like any other mismatch.
A checkout holding commits made on the host that origin does not have is
refused first, with its own message: `./run ship` gates origin's head and
could never cover them, so push them to `smarks/tarmar-canon` or drop them by
hand. The comparison is made only when the deploy would change something: with
no new commit on `main`, the checkout at the commit `.deploy-canon` records,
and origin's canon at or behind that commit (so the fast-forward would change
nothing), the deploy exits 0 as up to date whatever the record says, so a run
with nothing to do never shows red. `--force` reads no record, so it makes no comparison.

Three things follow from both colours reading one checkout. The canon's pages
are read per request, so the live colour serves canon pages from the moment
the deploy moves the checkout to them, while its nav, maps and prompt (read at
import, or built from the rules files on first use and then held for the
process) change only when it restarts. `deploy.sh rollback` switches the code
back but not the canon. And the GM retrieval index is rebuilt by
`build_gm_index` when the corpus hash changes, which a canon change alone does.

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

CI runs where the repository variable `CI_RUNNER` says (issue #964): on
GitHub's machines when it is unset, on datahorde's container runner when it is
`datahorde`. The commit hook no longer runs pytest or Playwright, so a commit
that skipped `./run gate` and never went through a pull request has had no
suite run on it at all. `./run gate` is the check that stands between a merge
and a deploy whatever CI says, and the only one while CI is not running, for
instance with `CI_RUNNER=datahorde` and no runner online there, where jobs
queue rather than run.

```bash
./run gate                 # run everything, record the commit, push the record, deploy
./run gate --no-deploy     # ...but do not start the Deploy workflow
./run gate --check         # does HEAD already have a record?
./run gate --check <sha>   # ...or that commit
./run gate --no-push       # record locally only (a deploy host will not see it)
```

**To deploy, run `./run ship`** after merging: it gates the head of `main`
against the canon's head in a temporary worktree, follows the Deploy run the
gate starts, and removes what it made. `docs/repositories.md` lists every
repository a deployment depends on and how a change in each reaches
production.

**The gate starts the deploy.** When the recorded commit is still the head of
`main` on GitHub and the first pass ran the Tarmar canon, `./run gate` starts
the Deploy workflow once the record is pushed (`gh workflow run deploy.yml
--ref main -f command="--expect <sha>"`). Deploy no longer starts on push: a
merge has no record yet, so every push-started run refused and showed red
(Spencer, 2026-10-10). A pass on an older commit, or one that ran only the
default canon, starts nothing; a Tarmar-canon gate run on the head deploys
it. `--expect` tells `deploy.sh` which commit was vouched for: if `main` has
moved by the time the run starts (it may wait behind another deploy),
`deploy.sh` deploys the new head only if it has its own Tarmar-canon record,
and otherwise stands down and exits 0. If the workflow will not start, the
gate exits 1 with the record kept, and Deploy can be started from the
Actions tab.

It runs, in order: `ruff check`, `ruff format --check`, `pyright`,
`makemigrations --check --dry-run`, `build_guides.py --check`, `pytest -q`,
`npx jest`, and Playwright through `scripts/update_js_coverage_threshold.py
--check` on its own `E2E_PORT` (so a dev server on 8000 neither answers
the suite's requests nor loses its own). It then runs pytest, Jest and
Playwright a second time, with `TARMAR_CANON_DIR` set to the absolute path of
`reference/default_canon` and Playwright on a second `E2E_PORT`. Each pass's
three steps are named for the canon it serves, `pytest (Tarmar canon)` and so
on for the first (or `(default canon)` when it ran that one), and
`pytest (default canon)` and so on for the second; the run's opening line
names the canon each port serves from the same label (issue #10). Each port,
and the fake Ollama's port 1000 above it, is picked free from 8390 upward
(starting at an offset derived from the checkout's path, so two worktrees'
gates seldom reach for the same pair), and the note records both as
`e2e-port` and `default-canon-e2e-port`.
`--e2e-port` and `--default-canon-e2e-port` name them instead; a named port
something is already listening on is refused, as is a picked one taken
before its browser step starts, since Playwright would test that server's
code (issue #966). That second pass
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
steps: ruff, ruff format, pyright, makemigrations, build_guides, pytest (Tarmar canon), jest (Tarmar canon), playwright (Tarmar canon), pytest (default canon), jest (default canon), playwright (default canon)
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

From the Mac, after merging:

```bash
./run ship
```

That gates the head of `main` and starts the Deploy workflow, which runs
`./deploy.sh --expect <sha>` on this host. Running `./deploy.sh` here by hand
still works, but collides on the deploy lock with any Deploy run in progress.

`deploy.sh` will:
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
| `./deploy.sh --expect <sha>` | Deploy, but stand down (exit 0) if `main` has moved past `<sha>` to a commit with no Tarmar-canon gate record; what the gate's Deploy run sends |
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

The nightly run (`deploy.sh backup --verify`) then restores its dump into a
throwaway `tarmar_restore_check_<pid>` database, counts `django_migrations`,
and drops it again; an exit trap drops it however the check ends. It does not
connect as the app's role. Creating a database needs `CREATEDB`, which the
role in `DATABASE_URL` does not have and should not get, so the check runs as
a role of its own, `tarmar_restore_check`, that holds `CREATEDB` and owns
nothing but the scratch databases it makes (issue #913). `pg_dump` still reads
as the role in `DATABASE_URL`.

Its connection lives in `.env.restore-check` in the main checkout, a file of
its own holding one line, `RESTORE_CHECK_DATABASE_URL`, which names the
`postgres` maintenance database on the same server. Only the restore check
reads it. It is kept out of `.env` because the units load `.env`: there, the
running app could have read a role that can create databases. Create the role
and the file once, generating the password on the host so it is never shown
(`openssl rand -hex` gives a value the env reader accepts unquoted):

```bash
cd /home/sam/dev/tarmar-studio
umask 077
password=$(openssl rand -hex 24)
printf "SET log_min_error_statement = panic;\nCREATE ROLE tarmar_restore_check LOGIN CREATEDB PASSWORD '%s';\n" \
    "$password" | sudo -u postgres psql --quiet -v ON_ERROR_STOP=1
printf 'RESTORE_CHECK_DATABASE_URL=postgres://tarmar_restore_check:%s@localhost:5432/postgres\n' \
    "$password" > .env.restore-check
unset password
chmod 600 .env .env.restore-check
```

The `SET` keeps a failed `CREATE ROLE` from being written to the server log
with the password in it. The stock `pg_hba.conf` lines for `127.0.0.1` and
`::1` (`scram-sha-256`, all users) admit the role exactly as they admit
`tarmar`. `.env`, `.env.restore-check` and any `.env.bak-*` copy hold
credentials: keep them mode 600, and `.gitignore` keeps them out of git. To
remove the role, delete `.env.restore-check` and run
`sudo -u postgres psql -c 'DROP ROLE tarmar_restore_check'`.

Without `.env.restore-check`, or without the variable in it, the check stops
with a message pointing at this file, and `tarmar-backup.service` ends
`failed` even though the dump itself was written.

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

`deploy.sh` warms the GM prompt cache at the end of every deploy (`manage.py
warm_gm`). A deploy that changes the prompt's text, by its code or by the
canon's rules files, whose numbers the prompt states (issue #68), stores one
new `GMPromptSnapshot` per prompt on its first turn and starts Ollama's prefix
cache afresh, which the warm-up refills. The other half, warming after Ollama
itself restarts, is a LaunchAgent on the Mac:
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
