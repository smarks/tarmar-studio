# The Games Master LLM host

The GM model does not run on the deploy host. It runs on a Mac Studio on the
same LAN, under Ollama, and the Django app reaches it over HTTP. This directory
holds the Mac side of that arrangement, so it lives under review beside
`deploy.sh` rather than only in one machine's home directory.

## What broke, and what these files fix

Issue #330 asked for the GM prompt cache to be warmed "after each deploy **and**
after Ollama restarts". Only the deploy half shipped: `deploy.sh` ends with
`manage.py warm_gm`, and nothing ran it when Ollama itself restarted. On the
Mac's first reboot as the LLM host, Ollama came back with no model loaded, the
first GM request had to load 24 GB and prefill a ~9.6k-token prompt, and the web
app reported the Game Master as unreachable (#386).

A second failure was latent in the same place. The deploy host's `.env` carried
the Mac's literal address, and the Mac takes that address by DHCP with no
reservation, so a new lease would have broken the link with nothing to repair
it.

## What the agent does

`tarmar-llm-ready.sh` runs at login and every 15 minutes:

1. Waits for Ollama to answer `/api/version`. If it stays silent for 30 seconds
   the script starts the Ollama agent itself — `launchctl bootstrap` when that
   agent is not registered at all, `kickstart` when it is registered but wedged.
2. Publishes the Mac's current `en0` address into the deploy host's `/etc/hosts`
   under a stable name, rewriting the entry only when it is missing or stale.
   The deploy host's `OLLAMA_BASE_URL` names that host rather than an address,
   so a new DHCP lease needs no `.env` edit and no unit restart.
3. Runs `manage.py warm_gm` on the deploy host over ssh when the GM model is not
   in `/api/ps`. The warm-up logic stays in the management command; this script
   only decides when to call it.

It is a no-op when the address is unchanged and the model is resident.

Measured on 2026-09-11, `qwen3.6:27b-mlx` on an M3 Ultra:

| Starting state | To model resident |
| --- | --- |
| Everything torn down, agent booted out, nothing listening | 80 s |
| Ollama restarted cold, no model loaded | 46 s |
| Healthy | no-op |

## Installing it

Both files carry absolute paths, because launchd expands nothing. Change
`/Users/sam` and the `EnvironmentVariables` block if the account or the network
differs.

```sh
mkdir -p ~/bin ~/Library/Logs/ollama
cp scripts/llm-host/tarmar-llm-ready.sh ~/bin/
chmod +x ~/bin/tarmar-llm-ready.sh
cp scripts/llm-host/com.origamisoftware.tarmar-llm-ready.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.origamisoftware.tarmar-llm-ready.plist
```

Run it once by hand to check it: `launchctl kickstart -k
gui/$(id -u)/com.origamisoftware.tarmar-llm-ready`, then read
`~/Library/Logs/ollama/tarmar-llm-ready.log`.

## What this does not cover

Both this agent and the Ollama one are *user* agents, so nothing serves until
the account logs in. FileVault means an unattended reboot waits at the unlock
screen in any case. Moving Ollama to a `LaunchDaemon` would cover the
logged-out case; it needs sudo, and it would move Ollama off the graphics path
these timings were measured on, so it has not been done.

The Ollama agent itself (`com.origamisoftware.ollama`) is still hand-managed on
the Mac and is not in this repo. Its settings are recorded in the vault's
`tarmar-studio` project note.

## Reviewing GM sessions sent to Claude

A Game Master can press **Send to Claude** (or type `/review <what to look at>`) in the GM chat. The server freezes the whole session into a review request (issue #402). `tarmar-gm-review.sh` runs every minute from the LaunchAgent beside it. With nothing queued it is one ssh round trip and no model at all. For each queued review it:

1. claims it on the deploy host, so two runs never take the same one;
2. fetches the frozen session into a fresh temporary folder, with the application source at `origin/main` exported beside it by `git archive` — the checkout's objects are read, its working tree is never touched. An export that fails or comes out empty fails the review rather than handing the model a transcript and no code (issue #678);
3. runs the Claude CLI in that folder, unattended, with the prompt in `gm-review-prompt.md`;
4. posts the findings back to the review, or marks it failed, and files the findings in the vault under `work/projects/gm-reviews/`.

The findings appear on the review's page in the app. To discuss them, open any Claude session and ask to discuss GM review N; `manage.py gm_review show N` gives the note, the frozen session and the findings.

The vault note puts the findings inside a fenced block under a line saying they are model output derived from text players typed (issue #679). The lockdown below covers the model *run*; it does not reach a session that later reads the note, and that session runs under the user's own settings. The fence is what tells such a reader, human or model, that the block is data rather than a request.

### Why the model run is locked down

A transcript contains text players typed. The user's own Claude settings default to bypassing permissions and allow writing files, shell commands, ssh and email tools, and an unattended run inherits all of that unless told otherwise. So the run is started with:

- `--tools Read Grep Glob` — no tool that writes, executes or reaches the network exists in the run at all;
- `--setting-sources project` and `--settings gm-review-settings.json` — the user's settings, hooks and allow rules are not loaded; reads are allowed in the review folder only, with keys and credentials denied outright;
- `--strict-mcp-config` with an empty server list — no MCP tools;
- `--permission-mode default` and no session persistence.

The script, not the model, does the claiming and the posting. Verified 2026-09-13 with a hostile prompt: a shell command, a file write and a read of `~/.ssh` all failed; a read in the folder worked. `scripts/test_llm_host.py` fails the build if any of those flags or settings loosen: it runs the script with a stand-in for the CLI and asserts the flags on the command line that was actually run, so a flag left behind in a comment beside a live invocation without it does not satisfy the check (issue #683).

### Installing it

```sh
mkdir -p ~/bin/tarmar-gm-review
cp scripts/llm-host/tarmar-gm-review.sh ~/bin/
chmod +x ~/bin/tarmar-gm-review.sh
cp scripts/llm-host/gm-review-prompt.md ~/bin/tarmar-gm-review/prompt.md
cp scripts/llm-host/gm-review-settings.json ~/bin/tarmar-gm-review/settings.json
cp scripts/llm-host/com.origamisoftware.tarmar-gm-review.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.origamisoftware.tarmar-gm-review.plist
```

Log: `~/Library/Logs/tarmar-gm-review.log`. A review that runs longer than 30 minutes is stopped and marked failed; one whose reviewer died mid-run becomes claimable again after an hour.
