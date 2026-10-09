# The CI runner on the Mac Studio

`ci.yml` runs on a self-hosted runner labelled `self-hosted, tarmar` (issue
#576, PR #585). GitHub bills no minutes for a self-hosted runner, which is what
let CI come back after it was disabled for billing on 2026-09-13. The workflow
stays disabled until the installer below registers the runner and enables it;
until then `./run gate` before each deploy is the only check.

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
`/Users/<developer>`: macOS makes a home `drwxr-x---` owned by the developer
and `staff`, which is why the runner is not in `staff`, and the installer
refuses to continue if the runner account can read the developer's home. What
a job can still reach is every local network service, because they listen on
loopback for any account: Ollama on `:11434` (the production Games Master
model) and a local Postgres. Postgres is the one that matters: Homebrew's
`pg_hba.conf` ships with `trust` for local connections, the server runs as the
developer, and that role is a superuser, so a job could connect without a
password and run commands as the developer. The installer refuses while
`pg_hba.conf` holds a `trust` line, and, once the account exists, while
`tarmarci` can actually connect as you over TCP or the socket, whatever the
file says. Change those lines to `scram-sha-256` (`peer` for the unix socket),
give the role your `DATABASE_URL` uses a password, and reload Postgres first.
Anything else you run on loopback (a `python -m http.server`, for instance) is
reachable by a job too.

## Installing it

From your own account, in a checkout of the repository, once:

```bash
sudo scripts/ci-runner/install-runner.sh
```

It needs root to create the account and install the daemon, and it needs to be
run *through* `sudo` from the account logged in to `gh`, because the
registration token comes from that login (through the keyring, so from a
desktop session rather than over ssh). What it does, each step skipped when
already done:

0. Refuses if `pg_hba.conf` trusts local connections (above).
1. Creates the group and the account `tarmarci` (hidden from the login
   window, random password) and its home, then checks the account cannot read
   your home.
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
