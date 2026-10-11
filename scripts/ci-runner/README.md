# The CI runner on the Mac Studio

**Retired 2026-10-10.** The code repository became
`smarks/tarmar-studio-private` on 2026-10-09 (its old name went to a public
snapshot), and on 2026-10-10 development moved to the public
`smarks/tarmar-studio`; the private one became the archive. A runner on a
public repository would run any fork's pull request on this Mac (issue #961),
so CI is GitHub-hosted by default and the one self-hosted option is the runner
on datahorde (`scripts/ci-runner-linux/`). `install-runner.sh` now refuses
outright, naming that; `--dry-run` still prints the old plan. The scripts stay
for the record and are not deleted.

**Since #964 no CI job is sent here.** `ci.yml` runs on GitHub's machines
unless the repository variable `CI_RUNNER` is `datahorde`, which selects a
runner on datahorde; neither matches this runner's labels. It stays installed
until that runner replaces it; `protect-main.sh` and `repository.sh` still
apply to whichever runs.

Until #964, `ci.yml` ran on this self-hosted runner, labelled
`self-hosted, tarmar` (issue #576, PR #585). GitHub bills no minutes for a
self-hosted runner, which is what let CI come back after it was disabled for
billing on 2026-09-13. What follows describes that setup.

## One run of the test job, against the default canon

The `test` job in `ci.yml` reports one required check, `test (default
canon)`. Since issue #921 the Tarmar canon lives in the private repository
`smarks/tarmar-canon`, and CI holds no credentials for it: the job leaves
`TARMAR_CANON_DIR` unset, the checkout has no canon of its own, and
`reference.paths` falls back to `reference/default_canon`, the generic world
every public checkout runs on. So every push proves that configuration passes
lint, pyright, the guide check (`build/` holds only the player-facing guides,
the same under any canon), pytest, Jest and Playwright, on `E2E_PORT` 8290 with
the fake Ollama at +1000. `protect-main.sh` requires that one check.

The Tarmar canon is not checked here. `./run gate`, run with
`TARMAR_CANON_DIR` pointing at a clone of the canon, runs its first pass
against it and its second against the default canon (DEPLOY.md), and a deploy
needs the gate's record. Issue #920 ran this job twice, the first entry with
the checkout's own canon; once the canon left the checkout that entry repeated
the second, so #921 dropped it.

## Why its own account

A CI job runs whatever a pull request says. The account that develops on this
Mac holds the SSH key to the deploy host, the production `.env` and database
behind it, and the `gh` token. A runner under that account would hand all of
that to any branch. So the runner runs as `tarmarci`, a hidden standard account
in its own group `tarmarci`, with no keys, no token and no login password
anyone keeps, started by a LaunchDaemon.

What that does and does not close. A job as `tarmarci` cannot read into
`/Users/<developer>` only once that home is `drwx------`. macOS creates a home
`drwxr-x---`, owned by the developer and group `staff`, and it makes every
local account a member of `staff` through `localaccounts`, whatever the
account's primary group: `tarmarci`'s own group does not keep it out
(`dsmemberutil checkmembership -U tarmarci -G staff` answers "user is a member
of the group"; issue #946). So `chmod 700` on your home is required, and the
installer checks for it before it creates the account and refuses until it is
done. It checks again once the account exists, as `tarmarci`, in case an ACL
still opens the home. `chmod 700` also ends guest access to `~/Public`, the
folder macOS file sharing offers by default. What
a job can still reach is every local network service, because they listen on
loopback for any account: Ollama on `:11434` (the production Games Master
model) and a local Postgres. Postgres is the one that matters: Homebrew's
`pg_hba.conf` ships with `trust` for local connections, the server runs as the
developer, and that role is a superuser, so a job could connect without a
password and run commands as the developer. The installer refuses while
`pg_hba.conf` holds a `trust` line, and, once the account exists, while
`tarmarci` can actually connect as you over TCP or the socket, whatever the
file says. Each of those two attempts runs `psql -w`, so it fails rather than
asking for a password a CI job would not have, and prints Postgres's own
reason for refusing (issue #947). Change those lines to `scram-sha-256`
(`peer` for the unix socket), give the role your `DATABASE_URL` uses a
password, and reload Postgres first.
Anything else you run on loopback (a `python -m http.server`, for instance) is
reachable by a job too.

## Installing it

Both scripts take the repository's name from `repository.sh`. As the note at
the top says, `install-runner.sh` refuses to install on this Mac. The opt-in
rule in `repository.sh` (`require_self_hosted_runner_opt_in`) now guards the
datahorde registration, `scripts/ci-runner-linux/register-from-mac.sh`: it
stops unless GitHub reports both that the repository variable `CI_RUNNER` is
`datahorde` (the opt-in to a self-hosted runner; `ci.yml` uses GitHub's
machines otherwise) and that the repository's fork-workflow approval policy
is `all_external_contributors` ("Require approval for all outside
collaborators", Settings > Actions > General; read from
`repos/<repository>/actions/permissions/fork-pr-contributor-approval`).
`protect-main.sh` needs neither: branch protection suits a public repository.

From your own account, in a checkout of the repository, once:

```bash
sudo scripts/ci-runner/install-runner.sh
```

It needs root to create the account and install the daemon, and it needs to be
run *through* `sudo` from the account logged in to `gh`, because the
registration token comes from that login (through the keyring, so from a
desktop session rather than over ssh). What it does, each step skipped when
already done:

0. Refuses unless your home is `drwx------` (`chmod 700 ~` first; above), and
   if `pg_hba.conf` trusts local connections (above).
1. Creates the group and the account `tarmarci` (hidden from the login
   window, random password) and its home, then checks, as that account, that
   it cannot read your home or connect to Postgres as you.
2. Downloads `actions/runner` **v2.337.0** for osx-arm64 into
   `/Users/tarmarci/actions-runner-tarmar` and checks the tarball against the
   SHA256 GitHub publishes in the release notes.
3. Registers it with the repository as `mac-studio-tarmar`, labels
   `self-hosted,macOS,tarmar`, with `/opt/homebrew/bin` at the front of the
   PATH it records, so jobs find `uv`, `node` and `npx`.
4. Copies your Playwright browser cache to the runner account, since
   `playwright install` hangs extracting under Node 26 on this Mac and the
   workflow installs no browser. After a Playwright upgrade, delete
   `/Users/tarmarci/Library/Caches/ms-playwright` and run the installer again.
5. Installs `com.origamisoftware.tarmar-ci-runner.plist` into
   `/Library/LaunchDaemons` and loads it, which starts the listener.
6. Waits for the runner to report online, then enables the CI workflow if it
   is disabled.

`--dry-run` prints every command instead of running it and needs no root;
`--dry-run --fresh` prints the whole plan as if nothing were installed.

Then push a commit, or re-run a workflow from the Actions tab, and watch it
go green. When it has:

```bash
scripts/ci-runner/protect-main.sh
```

requires both `test` checks, posted by GitHub Actions, before anything merges to
`main`, holds admins to it too (everyone who pushes here is the owner), and
refuses force-pushes and deletion. It requires no reviews: the pair-programming
rule in the house `CLAUDE.md` is the review gate.

## Where things are

| what | where |
| --- | --- |
| runner and its `_work` directory | `/Users/tarmarci/actions-runner-tarmar` |
| daemon | `/Library/LaunchDaemons/com.origamisoftware.tarmar-ci-runner.plist` |
| listener logs | `/Users/tarmarci/Library/Logs/tarmar-ci-runner/{stdout,stderr}.log` |
| job logs | `_work/_diag/` under the runner directory, and the Actions tab |

Restart: `sudo launchctl kickstart -k system/com.origamisoftware.tarmar-ci-runner`.

Remove, in this order, because `KeepAlive` restarts a deregistered listener
every 30 seconds until the daemon is gone:
`sudo launchctl bootout system/com.origamisoftware.tarmar-ci-runner`, then as
`tarmarci` `./config.sh remove --token <removal token>`, then
`sudo sysadminctl -deleteUser tarmarci` and
`sudo dseditgroup -o delete tarmarci`.

FileVault is on, so after a reboot nothing runs until someone unlocks the disk
at the login screen, which is itself a login; the daemon starts then and keeps
running after that person logs out.

While the runner is down, nothing can merge to `main`, because admins are
held to the check too. To lift that for an emergency merge and put it back:

```bash
gh api -X DELETE repos/smarks/tarmar-studio/branches/main/protection/enforce_admins
gh api -X POST   repos/smarks/tarmar-studio/branches/main/protection/enforce_admins
```

## Not verified by the tests

`scripts/test_ci_runner.py` holds the installer, the daemon, `ci.yml` and the
protection script together, runs the installer's full dry-run plan, and checks
it refuses a trusting `pg_hba.conf`. It cannot create the account or start the
daemon. Settled only by the first real run:

- Playwright's headless Chromium under a LaunchDaemon, which has no window
  server session. Headless Chromium is documented to need none; if the e2e
  step fails with a display or sandbox error, the fallback is the runner's own
  `svc.sh install` as a LaunchAgent for `tarmarci` with that account set to log
  in automatically, which costs the "nobody logged in" property.
- `uv sync` populating `/Users/tarmarci/.cache/uv` and `uv python install`
  on first run: the job's first run is slow and every later one is not.
- `sysadminctl -GID` on macOS 26 and its exit status on failure; the
  installer checks the account exists afterwards rather than trusting it.
