# The CI runner on datahorde, in one-job containers

`ci.yml` runs on GitHub's machines unless the repository variable `CI_RUNNER`
is `datahorde`; then it runs here (issue #964). Spencer's plan: GitHub's
machines while the month's Actions minutes last, this runner after.

```bash
gh variable set CI_RUNNER --body datahorde --repo smarks/tarmar-studio
```

```bash
gh variable delete CI_RUNNER --repo smarks/tarmar-studio
```

The required check keeps its name, `test (default canon)`, in both modes, so
branch protection holds either way. With `CI_RUNNER=datahorde` and this runner
down, CI queues and nothing can merge.

## Why a container, and one per job

Datahorde is the production host. A CI job runs whatever the branch under
test says, including whatever its npm and PyPI packages do. On the bare host
it could reach production: Postgres listens on loopback and on the LAN address
(192.168.4.163:5432), Redis and the app on loopback, and `/home/sam` is
world-readable. A long-lived runner would also let one bad job steal the
runner's credentials, or replace the runner, and fake every later result.

So each job gets its own container, which:

- is registered for that one job only. Before each start the service mints a
  just-in-time registration (`jit-config.sh`); the runner takes one job,
  GitHub removes it, the container exits and is deleted (`--rm`), and systemd
  starts a fresh one. No credential or file outlives the job;
- has no Docker socket (the socket is root on the host) and no host paths;
- sits on its own network, `tarmar-ci` on bridge `br-tarmar-ci`.
  `firewall.sh` keeps an nftables table of its own, `inet tarmar_ci`, that
  drops whatever the bridge sends to the host on any address, whatever it
  would send on to a private network (the LAN, where the Mac Studio's Ollama
  listens, and Tailscale), and all IPv6. ufw and Docker reload their own
  iptables chains, not this table, and a drop there is final. The container
  also has IPv6 turned off. What is left is the public internet, for GitHub,
  PyPI and npm;
- has no Linux capabilities, no privilege escalation, a non-root account on a
  uid nothing on datahorde uses (64001), and at most 4 CPUs, 16 GB of memory
  with no extra swap, 4,096 processes and a low block-I/O weight;
- keeps everything on Docker's own fixed-size store, a 30 GB file mounted at
  `/var/lib/tarmar-ci-docker`, so a job cannot fill the disk production's
  database lives on.

`install.sh` proves the walls before it starts the runner. From a container on
the CI network it connects to the host's ssh and Postgres on its LAN address,
the bridge's gateway, and, when the host itself can reach it, the Mac Studio's
Ollama; each must time out. It also checks that the container has no IPv6 and
can reach GitHub. It refuses to start the runner if any check fails.

## What is still shared

- **The token.** The fine-grained token that mints registrations lives on the
  host in `/etc/tarmar-ci-runner/github-token`, readable by root only, and
  never enters a container. It can manage this repository's runners, nothing
  else. When it expires, the service keeps retrying about once a minute, CI
  queues, and the journal says GitHub refused; make a new token and rerun
  `register-from-mac.sh`. The installer tests the token before it starts the
  service.
- **The wall's table.** ufw and Docker reloads leave it alone, but a full
  `flush ruleset` (`nft -f /etc/nftables.conf`, or enabling
  `nftables.service`) removes it. `tarmar-ci-firewall.timer` puts it back
  within a minute, and every container start reapplies it first; a job
  running in that minute would be unwalled.
- **The public internet.** A job reaches production's public endpoints like
  any other internet client.
- **The machine.** The limits above keep a job from starving the site, not
  from slowing it. A full CI run is about 10 minutes of heavy CPU.
- **Installing Docker changes the host's networking.** It turns on IPv4
  forwarding and adds its own iptables chains. ufw keeps its default forward
  policy (drop). The CI bridge is the only Docker network.

## Installing it

First make the token on github.com: Settings > Developer settings > Personal
access tokens > Fine-grained tokens. Resource owner `smarks`; only the
repository `smarks/tarmar-studio`; repository permission
Administration: read and write; nothing else.

Then, from a Mac logged in to `gh`, in a checkout of the repository:

```bash
scripts/ci-runner-linux/register-from-mac.sh
```

It checks the repository has opted in (`CI_RUNNER` is `datahorde`) and holds
fork workflows for approval ("Require approval for all outside collaborators"),
asks for the token at a hidden prompt (press Return to keep the one datahorde
has), copies this directory to datahorde, and runs `install.sh` there with
sudo. `install.sh` creates the store, installs `docker.io` from Ubuntu's
archive if Docker is missing, builds the image, creates the network, installs
the firewall, the scripts and the unit as root-owned files, stores the token,
proves the walls, and starts the service.

Rerun it after a Playwright upgrade (bump `PLAYWRIGHT_VERSION` in the
Dockerfile to the version `package-lock.json` resolves; a test holds the two
together), a gh or runner upgrade, or to replace the token. A rerun restarts
the service, which stops a job in progress.

## Operating it

On datahorde: `systemctl status tarmar-ci-runner`, `journalctl -u
tarmar-ci-runner`. If containers fail at once (20 starts in 5 minutes), the
unit stops; fix the cause, then `systemctl reset-failed tarmar-ci-runner &&
systemctl start tarmar-ci-runner`. Stop taking jobs with `systemctl disable --now
tarmar-ci-runner`; the next start deletes any runner it left offline.

## What the job brings itself

The image holds only what needs root: the system libraries Chromium and
weasyprint load, git, and gh (the notify job uses it). The job installs uv,
Python 3.14, Node and the Playwright browser itself, exactly as on GitHub's
machines. `PLAYWRIGHT_BROWSERS_PATH` is left unset, so the browser lands in
`~/.cache/ms-playwright`, where `ci.yml` caches it.

## Not verified by the tests

`scripts/test_ci_runner_linux.py` holds these files to each other and to
`ci.yml`; the Runner image workflow builds the image on GitHub. Neither runs
the walls: only `install.sh` on datahorde does, every time it runs.
