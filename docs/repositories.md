# Tarmar Studio: its repositories, and the one command that deploys it

Written 2026-10-10; brought up to date 2026-10-11, after development moved
from the private repository to this public one. Everything below is what a
full deployment of Tarmar Studio at https://tarmar.origamisoftware.com depends
on, what each piece is responsible for, and how a change in each one reaches
production.

## The one command

After a change is merged to `main` of `smarks/tarmar-studio`, from any
checkout of it on the Mac whose `origin` is this repository (ship fetches
`main` from `origin`, and the gate pushes its record there):

```bash
./run ship
```

It deploys the head of `main` completely. In order, it:

1. checks `gh` is logged in, and removes anything an earlier, killed ship
   left behind, processes still running from its directory included;
2. fetches `main` of this repository and of `smarks/tarmar-canon`;
3. checks each head out as a temporary, detached worktree, so no checkout
   anyone is working in is touched;
4. installs the app's dependencies there (`uv sync --locked`, `npm ci`);
5. runs `./run gate` against the canon's head: ruff, pyright, the migration
   and guide checks, then pytest, Jest and Playwright twice (Tarmar canon and
   default canon). On green the gate records the commit in `refs/notes/gate`
   and starts the Deploy workflow for it;
6. follows that Deploy run to its end and reads its log for what it did;
7. stops anything still running from inside the temporary directory (the
   dev server Playwright starts runs in a process group of its own, so
   stopping the gate does not reach it; a process holding a terminal other
   than ship's, such as a shell someone opened there, is reported and left
   alone), then removes both worktrees and the directory, whatever happened,
   including Ctrl-C or a closed terminal.

It ends with one of: `<sha> is live` (exit 0); `<sha> was already live`
(exit 0); `stopped: ...` with the reason, nothing more deployed (exit 1), which
includes a Deploy that stood down because `main` moved on while it waited
(run `./run ship` again); `left behind: ...` when the deploy result stands but
something could not be removed or stopped (exit 3); `lost track of Deploy run
...` when GitHub kept failing ship's requests while it followed the run, so
whether it deployed is not known (exit 4; a failed request is retried with a
growing wait, five times, before ship gives up, and the run itself carries
on); or `interrupted` (exit 130), naming the Deploy run if one had already
started, since that run carries on.
`./run ship --rehearse` does everything but start Deploy: the full gate
runs and its pass is recorded, and production is not touched.

A ship takes about half an hour. Measured 2026-10-11 on the Mac Studio: the
gate's eleven steps took 7.5 minutes (each Playwright pass about 2.5), with
provisioning on top; a Deploy then runs 15 to 20 minutes on datahorde.

What it needs on the Mac: `gh` logged in to an account that can run the
repository's workflows; ssh access to GitHub (the gate pushes its record to
`origin` over it); `uv`, `npm`, and Homebrew's `glib` and `pango` (for
weasyprint); Playwright's Chromium in `~/Library/Caches/ms-playwright` for the
locked Playwright version; and a clone of `smarks/tarmar-canon` at
`~/dev/tarmar-canon` (or wherever `TARMAR_CANON_DIR` points). It never needs
ssh to datahorde: the Deploy workflow does that.

A merge on its own deploys nothing (since 2026-10-10): Deploy starts only from
a gate that passed on the head of `main`.

### Shipping a change that lives in another repository

| The change is in | Ship it by |
|---|---|
| `tarmar-studio` | merge the PR, then `./run ship` |
| `tarmar-canon` | push to its `main`, then `./run ship`. `deploy.sh` fast-forwards the canon on datahorde, and a moved canon is itself a reason to deploy |
| `tarmar-engine`, `tarmar-rules`, `hexarena` | tag a release there, raise the pin in `pyproject.toml` (and `uv.lock`) through a PR here, merge, then `./run ship` |
| `speak-friend`, `origami-common`, `origami-auth` | push to their `main`, run `uv lock --upgrade-package <name>` here through a PR (the lock holds the exact commit), merge, then `./run ship` |
| `ops-workflows`, `base_repo_template` | push to their `main`; the next Deploy or CI run uses it, since both are referenced `@main` |

## The repositories

All are under `github.com/smarks`.

### Deployed, and required

| Repository | Visibility | Responsible for | How it reaches production |
|---|---|---|---|
| `tarmar-studio` | public | The application: the Django apps (characters, battle, Games Master, reference), templates, static files, migrations. Also `deploy.sh`, the gate (`scripts/gate.py`), `./run ship`, the CI and Deploy workflows, the Mac Studio's Ollama agents (`scripts/llm-host/`) and the CI runners (`scripts/ci-runner*/`) | `deploy.sh` checks the gated commit out into the idle colour on datahorde (`~/dev/tarmar-studio-blue` or `-green`), migrates, and switches nginx to it |
| `tarmar-canon` | private | The Tarmar canon: the world, GM tools, magic mechanics and author notes the Games Master and the reference pages read | `deploy.sh` clones or fast-forwards it to `/home/sam/dev/tarmar-canon` on datahorde; both colours read it through `TARMAR_CANON_DIR` |
| `tarmar-engine` | public | The turn engine: the six-phase battle loop, actions, combat math, spells, the AI policy | A Python dependency pinned to a tag (`v0.9.11`), installed by `uv sync` |
| `tarmar-rules` | public | The d20 combat-resolution core: the weapon-against-armour matrix, modifiers, criticals | A Python dependency pinned to a tag (`v0.3.0`) |
| `hexarena` | public | Hex-grid primitives: coordinates, dice, pathfinding, layout | A Python dependency pinned to a tag (`v0.2.0`) |
| `speak-friend` | public | The passphrase gate in front of the site | A Python dependency, its commit fixed by `uv.lock` |
| `origami-common` | public | Shared Django web utilities | A Python dependency, its commit fixed by `uv.lock` |
| `origami-auth` | public | The login app: register, log in, log out, profile | A Python dependency, its commit fixed by `uv.lock` |

### Needed to deploy, not deployed

| Repository | Visibility | Responsible for |
|---|---|---|
| `ops-workflows` | public | `deploy-blue-green.yml`, the reusable workflow the Deploy workflow calls: it connects to datahorde with the `DEPLOY_SSH_KEY` secret and runs `./deploy.sh --expect <sha>` there |
| `base_repo_template` | public | The `notify` action at the end of CI and Deploy, meant to email on failure or on the first green after a red. It sends nothing at present: `tarmar-studio`'s only secret is `DEPLOY_SSH_KEY`, with no `MAIL_PASSWORD`, so the notify step in both CI and Deploy does nothing. Failure emails that do arrive are presumably GitHub's own notifications (not checked) |

### Not part of the deployment

| Repository | Visibility | Responsible for |
|---|---|---|
| `tarmar-studio-private` | private, archived | Where the application was developed until 2026-10-10. Archived and read-only, and nothing deploys from it. Its closed issues and pull requests stay there. Its open issues were recreated in `tarmar-studio` and closed in the archive with a link to the copy; only the pinned notice of the move (`tarmar-studio-private#1002`) is still open there |
| `tarmar-feedback` | public | Retired 2026-10-10, replaced by the public repository's issue forms. Still exists; deleting it is Spencer's to run (`gh repo delete smarks/tarmar-feedback --yes`) |

## The machines

| Machine | Role |
|---|---|
| datahorde (192.168.4.163, `roots.origamisoftware.com`) | Production: nginx, Postgres, the blue and green app checkouts and their systemd units, the canon clone, nightly backups |
| Mac Studio (192.168.4.158) | Serves the Games Master's model through Ollama, which datahorde reaches by the name `tarmar-llm`; the agents in `scripts/llm-host/` keep it warm. Where `./run ship` and the gate run. Also holds the self-hosted runner `mac-studio-tarmar`, registered to the archived private repository, so no workflow can use it |
| GitHub Actions | CI, on GitHub's machines unless the repository variable `CI_RUNNER` is `datahorde`, which sends it to the container runner in `scripts/ci-runner-linux/`. No runner is registered for it yet, so with `CI_RUNNER=datahorde` CI would queue and nothing could merge. Because the repository is public, a self-hosted runner would run forks' pull requests on datahorde: `register-from-mac.sh` stops unless `CI_RUNNER` is set and the repository holds every outside contributor's workflow runs for approval (set as of 2026-10-11), and the runner's firewall keeps jobs off the LAN. Also runs the Deploy workflow |

## Rolling back, and checking what is live

```bash
gh workflow run deploy.yml --repo smarks/tarmar-studio --ref main -f command=status
```

```bash
gh workflow run deploy.yml --repo smarks/tarmar-studio --ref main -f command=rollback
```

`status` reports which colour is live and the commit each runs; `rollback`
switches back to the other colour, if its code knows every migration already
applied. Their output is in the run's log (`gh run view <run id> --log`), and
both wait behind a deploy that is already running. `DEPLOY.md` covers
`deploy.sh` in full.
