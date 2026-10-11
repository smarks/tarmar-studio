#!/bin/bash
# Register this Mac as the GitHub Actions runner for tarmar-studio (issue #576).
#
# Run once, as root, from the account that holds the gh login:
#
#     sudo scripts/ci-runner/install-runner.sh
#
# Everything the job does runs as its own account, tarmarci, in its own group,
# which holds no SSH key, no gh token and no way into the deploy host. That is
# the whole reason the runner is not simply registered under the account that
# develops here: a CI job runs whatever a pull request says, and this
# machine's own account can reach production. The installer checks the two
# ways a job could still get at that account's files and refuses if either is
# open: a readable home directory, and a local Postgres that trusts every
# local connection. The home has to be chmod 700 before the installer starts:
# macOS makes every local account, tarmarci included, a member of staff,
# whatever its primary group (issue #946).
#
# Idempotent: every step checks what is already there and skips it. Rerun it
# after a reboot that lost the daemon, or after a runner upgrade. A rerun
# reloads the daemon, which stops a job in progress, so do it between runs.
# --dry-run prints each command that would change the system and runs
# none of them, and needs no root; --fresh with it plans as if nothing were
# installed yet, so the plan does not depend on the machine's state.
#
# Bash 3.2 (macOS ships no newer one); no associative arrays, no ${var,,}.

set -euo pipefail

# The one release this script installs. Bump both together: the checksum is
# the SHA256 GitHub publishes in the release notes for the osx-arm64 tarball.
RUNNER_VERSION="2.337.0"
RUNNER_SHA256="5a2cd92908a93d7276a194e1de6008099f3e7946f3f8e14aa7a1a7b4a31fdec2"

RUNNER_USER="tarmarci"
RUNNER_GROUP="tarmarci"
RUNNER_FULL_NAME="Tarmar CI"
RUNNER_HOME="/Users/${RUNNER_USER}"
RUNNER_DIR="${RUNNER_HOME}/actions-runner-tarmar"
RUNNER_NAME="mac-studio-tarmar"
# ci.yml selects the runner by these; scripts/test_ci_runner.py holds the two
# lists together.
RUNNER_LABELS="self-hosted,macOS,tarmar"
SERVICE_LABEL="com.origamisoftware.tarmar-ci-runner"
DAEMON_PATH="/Library/LaunchDaemons/${SERVICE_LABEL}.plist"
LOG_DIR="${RUNNER_HOME}/Library/Logs/tarmar-ci-runner"
# Homebrew first: the job needs uv, node and npx from there. The same value
# goes into the daemon's environment and into the runner's .path file.
RUNNER_PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
# By absolute path: sudo's secure_path may not carry Homebrew.
GH_BIN="/opt/homebrew/bin/gh"
POSTGRES_HBA="/opt/homebrew/var/postgresql@16/pg_hba.conf"

TARBALL_NAME="actions-runner-osx-arm64-${RUNNER_VERSION}.tar.gz"
TARBALL_URL="https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${TARBALL_NAME}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# REPOSITORY and require_private_repository (issue #961).
# shellcheck source=repository.sh
source "${SCRIPT_DIR}/repository.sh"
PLIST_SOURCE="${SCRIPT_DIR}/${SERVICE_LABEL}.plist"

DRY_RUN=false
FRESH=false
ALLOW_TRUSTED_POSTGRES=false
for argument in "$@"; do
    case "$argument" in
        --dry-run) DRY_RUN=true ;;
        --fresh) FRESH=true ;;
        --allow-trusted-postgres) ALLOW_TRUSTED_POSTGRES=true ;;
        -h|--help)
            sed -n '2,26p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "unknown argument: $argument" >&2
            exit 2
            ;;
    esac
done
if [ "$FRESH" = true ] && [ "$DRY_RUN" = false ]; then
    echo "--fresh only makes sense with --dry-run" >&2
    exit 2
fi

# sudo -u keeps the working directory, and the runner account cannot enter a
# directory under a chmod 700 home: a command run as it from there starts
# with "getcwd: cannot access parent directories", which is not the state a
# CI job starts in (issue #947). Every path below is absolute.
cd /

log() { printf '[ci-runner] %s\n' "$*"; }
fail() { printf '[ci-runner] ERROR: %s\n' "$*" >&2; exit 1; }

# Every command that changes the machine goes through here, so --dry-run can
# show the plan and a reader can grep for exactly what the script touches.
act() {
    if [ "$DRY_RUN" = true ]; then
        printf '[ci-runner] would run: %s\n' "$*"
    else
        "$@"
    fi
}

# "Is this already there?" checks, answered "no" under --fresh so the dry run
# plans every step whatever the machine holds.
present() {
    [ "$FRESH" = false ] && "$@"
}

# The account that invoked sudo owns the gh login; the runner user never does.
# A dry run has no sudo, so it stands in for the caller.
INVOKING_USER="${SUDO_USER:-}"
if [ "$DRY_RUN" = true ] && [ -z "$INVOKING_USER" ]; then
    INVOKING_USER="$(id -un)"
fi
INVOKING_HOME="/Users/${INVOKING_USER}"

if [ "$DRY_RUN" = false ]; then
    [ "$(id -u)" -eq 0 ] || fail "run this with sudo; it creates a user and installs a LaunchDaemon"
    [ -n "$INVOKING_USER" ] || fail "run it through sudo from the account that is logged in to gh, not from a root shell"
    [ "$INVOKING_USER" != "$RUNNER_USER" ] || fail "run it from your own account, not as ${RUNNER_USER}"
    [ "$(uname -m)" = "arm64" ] || fail "this installs the osx-arm64 runner; this Mac reports $(uname -m)"
    [ -f "$PLIST_SOURCE" ] || fail "missing ${PLIST_SOURCE}; run from a checkout of the repository"
    [ -x "$GH_BIN" ] || fail "no gh at ${GH_BIN}"
fi

# gh as the invoking user, with that user's config and keyring.
gh_as_invoker() {
    sudo -u "$INVOKING_USER" -H "$GH_BIN" "$@"
}

# Before anything is created: a runner on a public repository runs any
# fork's pull request on this Mac.
if [ "$DRY_RUN" = false ]; then
    require_private_repository gh_as_invoker
    log "${REPOSITORY} is private"
else
    log "would check that ${REPOSITORY} is private"
fi

# A directory chain owned by the runner user at every level. `install -d`
# with -o/-g applies them to the last component only and leaves the parents
# root-owned, so each level is created on its own.
owned_dir() {
    local path="$1"
    local parent
    parent="$(dirname "$path")"
    if [ "$parent" != "/" ] && [ "$parent" != "/Users" ] && ! present test -d "$parent"; then
        owned_dir "$parent"
    fi
    if ! present test -d "$path"; then
        act install -d -o "$RUNNER_USER" -g "$RUNNER_GROUP" -m 755 "$path"
    fi
}

# ------------------------------------------ 0. what a job must not reach
# The developer's home, before the runner account exists. A primary group of
# its own does not keep tarmarci out of staff: macOS makes every local
# account a member of staff through localaccounts, whatever its primary
# group, and creates a home drwxr-x--- with group staff. So the home itself
# has to shut everyone else out (issue #946). The account check in step 1
# tries it as the runner would; this one says what to do before anything is
# created.
HOME_MODE_MESSAGE="${INVOKING_HOME} must be drwx------ before the runner account exists. Run
    chmod 700 ${INVOKING_HOME}
and run this again. macOS puts every local account in staff, the runner's
included, and staff can read into a drwxr-x--- home. chmod 700 also ends
guest access to ~/Public, the folder macOS file sharing offers by default."
home_mode="$(stat -f '%Lp' "$INVOKING_HOME" 2>/dev/null || true)"
if [ -z "$home_mode" ] || ! [[ "$home_mode" =~ ^[0-7]+$ ]]; then
    if [ "$DRY_RUN" = true ]; then
        log "would check that ${INVOKING_HOME} is drwx------ (chmod 700)"
    else
        fail "could not read the mode of ${INVOKING_HOME}"
    fi
elif [ $((8#$home_mode & 8#077)) -ne 0 ]; then
    if [ "$DRY_RUN" = true ]; then
        log "would refuse: ${HOME_MODE_MESSAGE}"
    else
        fail "$HOME_MODE_MESSAGE"
    fi
else
    log "${INVOKING_HOME} is drwx------"
fi

# Local Postgres that trusts every local connection lets any account on this
# Mac connect as the developer, who is its superuser, and run commands as
# them. That undoes the whole point of a separate runner account, so the
# installer stops here until pg_hba.conf asks for a password.
POSTGRES_TRUST_MESSAGE="${POSTGRES_HBA} trusts local connections, so a CI job could connect as the superuser.
Change its 'trust' lines to 'scram-sha-256' (and 'peer' for the unix socket),
set a password on the role your DATABASE_URL uses, reload Postgres, and run
this again. To register the runner anyway: --allow-trusted-postgres"
# The file is a hint: it names the lines to change. The check that decides
# is behavioural and runs once the runner account exists, below.
if [ -f "$POSTGRES_HBA" ] && grep -Eq '^\s*(local|host\w*)\s.*\strust(\s|#|$)' "$POSTGRES_HBA"; then
    if [ "$DRY_RUN" = true ]; then
        log "would refuse: ${POSTGRES_TRUST_MESSAGE}"
    elif [ "$ALLOW_TRUSTED_POSTGRES" = true ]; then
        log "WARNING: ${POSTGRES_HBA} trusts local connections; a job can act as the Postgres superuser"
    else
        fail "$POSTGRES_TRUST_MESSAGE"
    fi
else
    log "${POSTGRES_HBA} does not trust local connections (or is not there)"
fi

# What a job could do, tried as the job would: connect to local Postgres as
# the developer without a password, over TCP and over the socket. Any path
# and any pg_hba.conf, not only the one named above.
#
# -w (--no-password) on each attempt: a job knows no password, and without it
# psql asks for one on the terminal (/dev/tty, which no redirection stops)
# once pg_hba.conf wants scram-sha-256. A developer who answered that prompt
# turned the check into a connection with a password, and the refusal below
# into a false claim (issue #947). Each attempt's own error is printed, so a
# refusal says why Postgres refused, or what connected.
postgres_attempt_connects() {
    local psql_binary="$1"
    local over="$2"
    shift 2
    local error_output
    if error_output="$(sudo -u "$RUNNER_USER" env PGPASSWORD= PGCONNECT_TIMEOUT=3 \
        "$psql_binary" -w "$@" -U "$INVOKING_USER" -d postgres -Atc 'select 1' 2>&1 >/dev/null)"; then
        log "${RUNNER_USER} connected to Postgres as ${INVOKING_USER} over ${over}, with no password"
        return 0
    fi
    log "Postgres refused ${RUNNER_USER} as ${INVOKING_USER} over ${over}: ${error_output:-no message}"
    return 1
}

postgres_refuses_the_runner() {
    local psql_binary
    psql_binary="$(command -v psql 2>/dev/null || ls /opt/homebrew/opt/postgresql@*/bin/psql 2>/dev/null | head -1 || true)"
    if [ -z "$psql_binary" ]; then
        log "no psql found; skipping the connection check"
        return 0
    fi
    local connected=false
    if postgres_attempt_connects "$psql_binary" "TCP" -h 127.0.0.1; then
        connected=true
    fi
    if postgres_attempt_connects "$psql_binary" "the unix socket"; then
        connected=true
    fi
    [ "$connected" = false ]
}

# ---------------------------------------------------------------- 1. the user
# Its own primary group, so files the runner creates are not group staff. It
# does not keep the account out of staff: macOS adds every local account to
# staff through localaccounts, so the home check in step 0 is what keeps the
# developer's home shut (issue #946).
if present dscl . -read "/Groups/${RUNNER_GROUP}" >/dev/null 2>&1; then
    log "group ${RUNNER_GROUP} exists"
else
    log "creating group ${RUNNER_GROUP}"
    act dseditgroup -o create -r "$RUNNER_FULL_NAME" "$RUNNER_GROUP"
fi
if [ "$DRY_RUN" = true ] && ! dscl . -read "/Groups/${RUNNER_GROUP}" >/dev/null 2>&1; then
    runner_gid="<gid of ${RUNNER_GROUP}>"
else
    runner_gid="$(dscl . -read "/Groups/${RUNNER_GROUP}" PrimaryGroupID | awk '{print $2}')"
fi

if present id "$RUNNER_USER" >/dev/null 2>&1; then
    log "user ${RUNNER_USER} exists"
else
    log "creating user ${RUNNER_USER}"
    # A standard account with a random password nobody keeps: launchd starts
    # the runner as this user without one, and nothing should ever log in as it.
    # IsHidden keeps the login window from offering it. sysadminctl exits 0
    # on some failures, so the account is checked for afterwards.
    if [ "$DRY_RUN" = true ]; then
        throwaway_password="<random>"
    else
        throwaway_password="$(openssl rand -base64 30)"
    fi
    act sysadminctl -addUser "$RUNNER_USER" -fullName "$RUNNER_FULL_NAME" \
        -password "$throwaway_password" -home "$RUNNER_HOME" -shell /bin/zsh \
        -GID "$runner_gid"
    if [ "$DRY_RUN" = false ]; then
        id "$RUNNER_USER" >/dev/null 2>&1 || fail "sysadminctl did not create ${RUNNER_USER}"
    fi
    act dscl . -create "/Users/${RUNNER_USER}" IsHidden 1
fi
owned_dir "$RUNNER_HOME"
owned_dir "$LOG_DIR"
owned_dir "${RUNNER_HOME}/Library/Caches"

# The check that the isolation holds, tried as the runner: its account must
# not be able to read into the developer's home. Step 0 checked the mode; an
# ACL can still open a 700 home, and this is what a job would meet.
if [ "$DRY_RUN" = false ]; then
    # Read or traverse: a 711 home fails `test -r` and still opens by path.
    if sudo -u "$RUNNER_USER" test -r "$INVOKING_HOME" || sudo -u "$RUNNER_USER" test -x "$INVOKING_HOME"; then
        fail "${RUNNER_USER} can read into ${INVOKING_HOME} although it is drwx------; an ACL may open it (ls -le ${INVOKING_HOME}). Remove it, then rerun"
    fi
    log "${RUNNER_USER} cannot read into ${INVOKING_HOME}"
    if postgres_refuses_the_runner; then
        log "${RUNNER_USER} cannot connect to Postgres as ${INVOKING_USER}"
    elif [ "$ALLOW_TRUSTED_POSTGRES" = true ]; then
        log "WARNING: ${RUNNER_USER} can connect to Postgres as ${INVOKING_USER} without a password"
    else
        fail "${RUNNER_USER} connected to local Postgres as ${INVOKING_USER} with no password.
${POSTGRES_TRUST_MESSAGE}"
    fi
else
    log "would check that ${RUNNER_USER} cannot read into ${INVOKING_HOME}"
    log "would check that ${RUNNER_USER} cannot connect to Postgres as ${INVOKING_USER}"
fi

# ------------------------------------------------------------- 2. the runner
if present test -x "${RUNNER_DIR}/bin/Runner.Listener"; then
    log "runner binaries present at ${RUNNER_DIR}"
else
    log "downloading actions/runner v${RUNNER_VERSION}"
    owned_dir "$RUNNER_DIR"
    tarball="${RUNNER_DIR}/${TARBALL_NAME}"
    act sudo -u "$RUNNER_USER" /usr/bin/curl -fsSL -o "$tarball" "$TARBALL_URL"
    if [ "$DRY_RUN" = false ]; then
        actual_sha="$(shasum -a 256 "$tarball" | cut -d' ' -f1)"
        [ "$actual_sha" = "$RUNNER_SHA256" ] \
            || fail "checksum mismatch for ${TARBALL_NAME}: got ${actual_sha}, expected ${RUNNER_SHA256}"
        log "checksum verified"
    else
        log "would verify sha256 ${RUNNER_SHA256}"
    fi
    act sudo -u "$RUNNER_USER" tar -xzf "$tarball" -C "$RUNNER_DIR"
    act rm -f "$tarball"
    # The runner's own svc.sh installs its service wrapper at the runner root,
    # and the self-updater looks for it there; match that.
    act sudo -u "$RUNNER_USER" cp "${RUNNER_DIR}/bin/runsvc.sh" "${RUNNER_DIR}/runsvc.sh"
fi

# ------------------------------------------------------ 3. registration
if present test -f "${RUNNER_DIR}/.runner"; then
    log "runner already registered ($(tr -d '\n' < "${RUNNER_DIR}/.runner" | cut -c1-80))"
else
    log "registering with ${REPOSITORY} as ${RUNNER_NAME} [${RUNNER_LABELS}]"
    if [ "$DRY_RUN" = true ]; then
        log "would fetch a registration token: gh api -X POST repos/${REPOSITORY}/actions/runners/registration-token"
        registration_token="DRY-RUN-TOKEN"
    else
        registration_token="$(gh_as_invoker api -X POST "repos/${REPOSITORY}/actions/runners/registration-token" --jq .token)"
        [ -n "$registration_token" ] || fail "gh returned no registration token; is ${INVOKING_USER} logged in with admin on ${REPOSITORY}?"
    fi
    # config.sh records the PATH it sees into .path, and runsvc.sh exports
    # that on every start, which is how the job finds Homebrew's uv and node.
    act sudo -u "$RUNNER_USER" -H env PATH="$RUNNER_PATH" \
        bash -c "cd '${RUNNER_DIR}' && ./config.sh --unattended \
            --url 'https://github.com/${REPOSITORY}' \
            --token '${registration_token}' \
            --name '${RUNNER_NAME}' \
            --labels '${RUNNER_LABELS}' \
            --work _work \
            --replace"
fi

# ------------------------------------------- 4. the Playwright browser
# `npx playwright install` hangs extracting under Node 26 on this Mac, so the
# job never installs a browser; it uses the one already in the runner user's
# cache. Seed that cache from the invoking user's, which the suite here keeps
# current. A Playwright upgrade means running this again.
SOURCE_BROWSERS="${INVOKING_HOME}/Library/Caches/ms-playwright"
TARGET_BROWSERS="${RUNNER_HOME}/Library/Caches/ms-playwright"
if present test -n "$(ls -A "$TARGET_BROWSERS" 2>/dev/null)"; then
    log "Playwright browsers present at ${TARGET_BROWSERS}"
elif [ -d "$SOURCE_BROWSERS" ] || [ "$DRY_RUN" = true ]; then
    if [ ! -d "$SOURCE_BROWSERS" ]; then
        log "would fail: no Playwright browsers at ${SOURCE_BROWSERS}; run the e2e suite once as ${INVOKING_USER} first"
    fi
    log "copying Playwright browsers from ${SOURCE_BROWSERS}"
    owned_dir "$TARGET_BROWSERS"
    # Trailing /. copies the contents, not the directory into itself.
    act cp -R "${SOURCE_BROWSERS}/." "$TARGET_BROWSERS"
    act chown -R "${RUNNER_USER}:${RUNNER_GROUP}" "$TARGET_BROWSERS"
else
    fail "no Playwright browsers at ${SOURCE_BROWSERS}; run the e2e suite once as ${INVOKING_USER} first"
fi

# ------------------------------------------------------- 5. the daemon
# Loading it starts the listener (RunAtLoad), so there is no kickstart. On a
# rerun the old job is unloaded first, and launchd is given time to let go
# of the label before it is loaded again.
log "installing ${DAEMON_PATH}"
act install -o root -g wheel -m 644 "$PLIST_SOURCE" "$DAEMON_PATH"
if present launchctl print "system/${SERVICE_LABEL}" >/dev/null 2>&1; then
    # bootout can answer "Operation now in progress" while the job is still
    # stopping; the wait below is what decides whether it went.
    act launchctl bootout "system/${SERVICE_LABEL}" || true
    if [ "$DRY_RUN" = false ]; then
        for _attempt in $(seq 1 20); do
            launchctl print "system/${SERVICE_LABEL}" >/dev/null 2>&1 || break
            sleep 1
        done
        if launchctl print "system/${SERVICE_LABEL}" >/dev/null 2>&1; then
            fail "system/${SERVICE_LABEL} is still loaded 20 s after bootout; wait for the running job to finish and rerun"
        fi
    fi
fi
act launchctl bootstrap system "$DAEMON_PATH"

# ----------------------------------------------------- 6. is it online?
if [ "$DRY_RUN" = false ]; then
    log "waiting for ${RUNNER_NAME} to report online"
    online=false
    for _attempt in $(seq 1 30); do
        status="$(gh_as_invoker api "repos/${REPOSITORY}/actions/runners" \
            --jq ".runners[] | select(.name == \"${RUNNER_NAME}\") | .status" 2>/dev/null || true)"
        if [ "$status" = "online" ]; then
            online=true
            break
        fi
        sleep 2
    done
    [ "$online" = true ] || fail "runner is not online after 60 s; read ${LOG_DIR}/stderr.log"
    log "runner online"
    # `gh workflow enable` looks only among disabled workflows, so a second
    # run must not call it on one that is already active.
    ci_state="$(gh_as_invoker workflow list --all --repo "$REPOSITORY" --json name,state \
        --jq '.[] | select(.name == "CI") | .state')"
    if [ "$ci_state" = "active" ]; then
        log "CI workflow already enabled"
    else
        log "enabling the CI workflow"
        gh_as_invoker workflow enable CI --repo "$REPOSITORY"
    fi
else
    log "would wait for the runner to come online, then enable the CI workflow if it is disabled"
fi

log "done. Next: push a commit or re-run a workflow, watch it go green, then"
log "  scripts/ci-runner/protect-main.sh"
log "to require that check before anything merges."
