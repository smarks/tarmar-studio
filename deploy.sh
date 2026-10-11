#!/bin/bash
# Blue-Green Deployment Script for Tarmar Studio (Tarmar)
#
# Usage: ./deploy.sh [--force | --expect <sha> | rollback | status | backup]
#   --force:  Deploy even if no changes detected, and even if the commit has
#             no passing gate record
#   --expect <sha>:
#             The commit the caller vouched for (./run gate starts Deploy with
#             this). If main has since moved to a commit with no gate record,
#             stand down and exit 0: that commit's own gate run deploys it
#   rollback: Switch back to the standby colour, if its code knows every
#             migration already applied
#   status:   Show which colour is live and what each runs
#   backup [--verify]:
#             pg_dump the database into var/backups/ (the nightly timer's job).
#             --verify then restores the newest dump into a throwaway database
#             and reads a table back out of it
#
# This script:
#   1. Pulls the latest code into the main checkout (and re-runs itself from
#      the pulled copy, so a deploy always runs the script it just fetched)
#   2. Clones or fast-forwards the Tarmar canon checkout beside it (a moved
#      canon is itself a change to deploy) and checks .env names it
#   3. Checks out origin/main into the inactive colour's release checkout
#   4. Runs the verification checks against that checkout
#   5. Builds it (dependencies, static files, a database backup, migrations,
#      GM index)
#   6. Renders its systemd unit, restarts it, and verifies it
#   7. Switches traffic to it
#   8. Keeps the old colour running, on its own checkout, as instant rollback
#
# Layout on the host (issue #328):
#   /home/sam/dev/tarmar-studio          the main checkout: this script, .env,
#                                        .deploy-state, and the runtime state
#                                        both colours share (staticfiles/,
#                                        logs/, var/)
#   /home/sam/dev/tarmar-studio-blue     blue's release checkout (git worktree)
#   /home/sam/dev/tarmar-studio-green    green's release checkout (git worktree)
#   /home/sam/dev/tarmar-canon           the Tarmar canon, a clone of the private
#                                        smarks/tarmar-canon (issue #921); .env's
#                                        TARMAR_CANON_DIR names it, and both
#                                        colours read it
#
# Each unit runs from its own release checkout, so pulling the main checkout
# never changes the source a live worker imports from, and a deploy that fails
# leaves the live colour's files exactly as they were. setup-blue-green.sh
# sources this file for its helpers; the command dispatch at the bottom only
# runs when the script is executed directly.

set -e

APP_DIR="${TARMAR_APP_DIR:-/home/sam/dev/tarmar-studio}"
cd "$APP_DIR"
# Where uv lives on the deploy host. Overridable for the same reason
# TARMAR_APP_DIR is: the tests point it at a directory of stand-ins, and since
# this line goes to the front of PATH, a stand-in there wins over the real uv
# wherever the machine keeps it (/home/sam/.local/bin on datahorde,
# /opt/homebrew/bin on a Mac). Same pattern as DEPLOY_HOST_UV_PATH in
# scripts/llm-host/. Unset, which is every run on the host, nothing changes.
export PATH="${TARMAR_LOCAL_BIN:-/home/sam/.local/bin}:$PATH"

BLUE_PORT=9066
GREEN_PORT=9067
# Overridable for the same reason TARMAR_APP_DIR is: the tests drive a real
# switch, and its recovery path, against a file in a throwaway directory.
UPSTREAM_CONF="${TARMAR_UPSTREAM_CONF:-/etc/nginx/conf.d/tarmar-upstream.conf}"
STATE_FILE="$APP_DIR/.deploy-state"
# The Tarmar canon commit the last successful deploy put live (issue #921). A
# canon checkout at any other commit is a change to deploy, even if a deploy
# that pulled it failed before it went live.
CANON_STATE_FILE="$APP_DIR/.deploy-canon"
UNIT_TEMPLATE="tarmar.service.template"
# Runtime directories both colours read and write. nginx serves /static/ from
# the main checkout's staticfiles/ (nginx/tarmar.conf), the audit log and the
# GM retrieval index live under the main checkout too; each release checkout
# links to them rather than growing its own copy.
SHARED_DIRS="staticfiles logs var"
ENV_FILE="$APP_DIR/.env"
# The nightly restore check's own connection (issue #913), kept out of .env so
# the units, which load .env, never see a role that can create databases.
# Only verify_latest_backup reads it.
RESTORE_CHECK_ENV_FILE="$APP_DIR/.env.restore-check"
# The Tarmar canon (issue #921): the private repository whose root is the
# canon directory, cloned beside the main checkout (so /home/sam/dev/tarmar-canon
# on datahorde, which authenticates to GitHub as smarks over ssh). Both are
# overridable for the same reason TARMAR_APP_DIR is: the tests clone a
# throwaway origin into a throwaway directory instead.
CANON_REMOTE="${TARMAR_CANON_REMOTE:-git@github.com:smarks/tarmar-canon.git}"
CANON_CHECKOUT="${TARMAR_CANON_CHECKOUT:-$(dirname "$APP_DIR")/tarmar-canon}"
LOCK_FILE="$APP_DIR/.deploy.lock"
# Where `./run gate` records a commit whose suites all passed (issue #576).
GATE_NOTES_REF="refs/notes/gate"
BACKUP_DIR="$APP_DIR/var/backups"
BACKUP_KEEP=10
BACKUP_UNIT_TEMPLATE="tarmar-backup.service.template"
BACKUP_TIMER="tarmar-backup.timer"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log() { echo -e "${GREEN}[DEPLOY]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# One mutating command at a time -- deploy, rollback or backup (issues #576,
# #667). The lock used to live only in
# the Actions workflow's `concurrency:`, which a hand-run deploy never passes
# through, and two runs interleaving can flip nginx to a colour the other is
# mid-restart on (#319). flock -n fails at once rather than queueing: a second
# deploy should be run again deliberately, not silently after the first. The
# lock is on the open file, so it holds across the re-exec after `git pull`
# and is released when the script exits, however it exits.
#
# The re-executed copy must not take it again. `exec 9>` on a descriptor that
# already holds the lock closes the old open file description, which drops the
# lock, before opening a new one: the deploy would spend that instant unlocked,
# and a deploy started inside it would take the lock and leave the pulled copy
# to fail at its own `flock -n`. Descriptor 9 is inherited through the exec
# still holding the lock, so the pulled copy keeps it instead. TARMAR_DEPLOY_
# LOCKED is exported and travels with it; the write test confirms 9 really did
# arrive, so a lost descriptor takes the lock afresh rather than deploying with
# none.
take_deploy_lock() {
    if [ -n "${TARMAR_DEPLOY_LOCKED:-}" ] && { true >&9; } 2>/dev/null; then
        return
    fi
    exec 9>"$LOCK_FILE"
    flock -n 9 || error "deploy in progress (another deploy.sh holds $LOCK_FILE)"
    export TARMAR_DEPLOY_LOCKED=1
}

# A plain `git fetch` does not carry refs/notes/*, so ask for the gate's ref by
# name (issue #576). A remote with no gate notes yet is not an error here; the
# check below is what refuses the deploy.
fetch_gate_notes() {
    git fetch --quiet origin "+$GATE_NOTES_REF:$GATE_NOTES_REF" 2>/dev/null || true
}

# Refuse a commit whose suites nobody ran. `./run gate` runs ruff, pyright, the
# migration and guide checks, pytest, Jest and Playwright, and on a green run
# records the commit it ran on in $GATE_NOTES_REF. The record lives in git,
# fetched from origin, rather than in a file under the app dir: a file on this
# host could be stale or hand-written, and a deploy would wave it through.
require_gate_record() {
    local revision=$1
    local record
    fetch_gate_notes
    record=$(git notes --ref="$GATE_NOTES_REF" show "$revision" 2>/dev/null) || record=""
    if [ -z "$record" ]; then
        error "$revision has no passing gate record in $GATE_NOTES_REF.
Run './run gate' on that commit on a machine that can run the suites; it
records the commit and pushes the ref. To deploy it without one, anyway:
  ./deploy.sh --force"
    fi
    log "Gate record for $revision:"
    echo "$record" | sed 's/^/  /'
    # Production serves the Tarmar canon, so a record whose first pass ran the
    # default canon (a gate run without TARMAR_CANON_DIR) proves nothing about
    # it (issue #921).
    local gate_canon
    gate_canon=$(echo "$record" | sed -n 's/^canon: *//p')
    if [ "$gate_canon" != "tarmar" ]; then
        error "$revision's gate record ran against the ${gate_canon:-unnamed} canon, not the Tarmar canon.
Run './run gate' again with TARMAR_CANON_DIR set to a clone of smarks/tarmar-canon,
or deploy it anyway with: ./deploy.sh --force"
    fi
    GATE_CANON_COMMIT=$(echo "$record" | sed -n 's/^canon-commit: *//p')
}

# Whether $1 has a passing gate record that ran the Tarmar canon: what
# require_gate_record demands, asked without failing.
has_passing_tarmar_record() {
    local record
    fetch_gate_notes
    record=$(git notes --ref="$GATE_NOTES_REF" show "$1" 2>/dev/null) || return 1
    [ "$(echo "$record" | sed -n 's/^canon: *//p')" = "tarmar" ]
}

# When ./run gate started this deploy for $1 and main has since moved to $2
# (a merge while the run waited for its turn), deploy $2 only if it has its
# own record; otherwise there is nothing for this run to do, and exiting 0
# keeps a merge from showing as a failed deploy. The commit's own gate run
# starts its deploy (Spencer, 2026-10-10).
stand_down_if_main_moved() {
    local expected=$1 remote=$2
    [ -z "$expected" ] && return 0
    [ "$remote" = "$expected" ] && return 0
    if has_passing_tarmar_record "$remote"; then
        log "main moved past ${expected} to ${remote}, which has its own gate record; deploying ${remote}"
        return 0
    fi
    log "main moved past ${expected} to ${remote}, which has no Tarmar-canon gate record yet; its own gate run deploys it. Nothing to do."
    exit 0
}

# Bring the canon checkout up to date with origin's main (issue #921): clone it
# the first time, fast-forward it every time after. Anything else -- no access
# to the private repository, a checkout with local commits or edits that block
# the fast-forward -- stops the deploy before a release is built, since the
# site would otherwise serve whatever canon happened to be on disk. Sets
# CANON_MOVED=true when the checkout's HEAD is not the commit the last
# successful deploy put live ($CANON_STATE_FILE): a canon change is worth
# deploying even when this repository has none.
sync_canon() {
    local deployed=""
    [ -f "$CANON_STATE_FILE" ] && deployed=$(cat "$CANON_STATE_FILE")
    if [ -d "$CANON_CHECKOUT/.git" ]; then
        log "Updating the Tarmar canon in $CANON_CHECKOUT..."
        git -C "$CANON_CHECKOUT" fetch --quiet origin \
            || error "could not fetch the Tarmar canon into $CANON_CHECKOUT from its origin"
        git -C "$CANON_CHECKOUT" pull --quiet --ff-only \
            || error "the Tarmar canon in $CANON_CHECKOUT cannot fast-forward to its origin; resolve it by hand"
    elif [ -e "$CANON_CHECKOUT" ]; then
        error "$CANON_CHECKOUT exists but is not a git checkout of the Tarmar canon"
    else
        log "Cloning the Tarmar canon into $CANON_CHECKOUT..."
        git clone --quiet "$CANON_REMOTE" "$CANON_CHECKOUT" \
            || error "could not clone the Tarmar canon from $CANON_REMOTE"
    fi
    CANON_HEAD=$(git -C "$CANON_CHECKOUT" rev-parse HEAD)
    CANON_MOVED=false
    [ "$deployed" = "$CANON_HEAD" ] || CANON_MOVED=true
    log "Tarmar canon at $CANON_HEAD ($(git -C "$CANON_CHECKOUT" log -1 --format='%s'))"
    if [ "$CANON_MOVED" = true ]; then
        log "The live site was deployed with Tarmar canon ${deployed:-(none recorded)}"
    fi
    # The gate record says which canon commit the suites ran against; a
    # different one here has not been through them.
    if [ -n "${GATE_CANON_COMMIT:-}" ] && [ "$GATE_CANON_COMMIT" != "$CANON_HEAD" ]; then
        warn "the gate ran against Tarmar canon $GATE_CANON_COMMIT, but this deploy serves $CANON_HEAD; run './run gate' with the canon at $CANON_HEAD to cover it"
    fi
}

# Strip trailing slashes, so /a/b/ and /a/b name the same directory.
without_trailing_slashes() {
    local path=$1
    while [ "${path%/}" != "$path" ] && [ "$path" != "/" ]; do
        path=${path%/}
    done
    printf '%s' "$path"
}

# The value .env gives TARMAR_CANON_DIR, read through load_env_file in a
# subshell so nothing else in .env reaches this shell before the tests run.
env_file_canon_dir() {
    (load_env_file "$ENV_FILE" && printf '%s' "${TARMAR_CANON_DIR:-}")
}

# The units read the canon .env names, so it has to be the checkout
# sync_canon just updated: unset, production would serve the generic default
# canon; elsewhere, a canon this deploy never pulled. Checked before a release
# is built, so a bad .env stops the deploy with nothing to undo.
require_canon_dir() {
    local configured
    configured=$(env_file_canon_dir) || exit 1
    [ -n "$configured" ] \
        || error "TARMAR_CANON_DIR is not set in $ENV_FILE; set it to $CANON_CHECKOUT (DEPLOY.md)"
    [ "$(without_trailing_slashes "$configured")" = "$(without_trailing_slashes "$CANON_CHECKOUT")" ] \
        || error "TARMAR_CANON_DIR in $ENV_FILE is $configured, but this deploy updates $CANON_CHECKOUT"
}

# Export .env into this shell exactly as systemd's EnvironmentFile hands it to
# the units (issue #574). Sourcing it with bash, as this script used to, applies
# shell quoting, expansion and word splitting that systemd does not, so the
# same line could mean two different values to `migrate` and to the site. This
# is the only reader of .env in deploy.sh, and it accepts only the subset of the
# format both parsers agree on: blank lines, comments, and KEY=value where the
# value has no quotes, backslashes, `$`, backticks or whitespace. Anything else
# fails the deploy, naming the line and never printing its value, since .env
# holds secrets. Values are exported literally, never evaluated.
load_env_file() {
    local file=$1
    local line
    local number=0
    local key_pattern='^[A-Za-z_][A-Za-z0-9_]*='
    local plain_pattern='^[A-Za-z_][A-Za-z0-9_]*=[^"'\''\\$`[:space:]]*$'
    [ -f "$file" ] || error "$file not found"
    while IFS= read -r line || [ -n "$line" ]; do
        number=$((number + 1))
        if [[ "$line" =~ ^[[:space:]]*$ ]] || [[ "$line" =~ ^[[:space:]]*[#\;] ]]; then
            continue
        fi
        if [[ ! "$line" =~ $plain_pattern ]]; then
            if [[ "$line" =~ $key_pattern ]]; then
                error "$file line $number (${line%%=*}) is not a plain KEY=value: quotes, backslashes, \$, backticks and spaces read differently under systemd and bash"
            fi
            error "$file line $number is not a KEY=value line"
        fi
        export "$line"
    done < "$file"
}

# Get current active environment
get_active() {
    if [ -f "$STATE_FILE" ]; then
        cat "$STATE_FILE"
    else
        echo "blue"  # Default to blue
    fi
}

# Get inactive environment
get_inactive() {
    if [ "$(get_active)" = "blue" ]; then
        echo "green"
    else
        echo "blue"
    fi
}

get_port() {
    if [ "$1" = "blue" ]; then
        echo "$BLUE_PORT"
    else
        echo "$GREEN_PORT"
    fi
}

# Where a colour's release checkout lives
release_dir() {
    echo "${APP_DIR}-$1"
}

# Point $release/$name at the main checkout's copy of a shared runtime dir
link_shared_dir() {
    local release=$1
    local name=$2
    local target="$APP_DIR/$name"
    mkdir -p "$target"
    if [ -e "$release/$name" ] && [ ! -L "$release/$name" ]; then
        error "$release/$name exists and is not a symlink; move it aside so it can point at $target"
    fi
    ln -sfn "$target" "$release/$name"
}

# Put a colour's release checkout at origin/main, creating it if needed
prepare_release() {
    local colour=$1
    local release
    release=$(release_dir "$colour")

    git worktree prune
    if [ ! -e "$release/.git" ]; then
        [ -e "$release" ] && error "$release exists but is not a git worktree"
        log "Creating $colour release checkout at $release..."
        git worktree add --detach "$release" origin/main
    else
        log "Checking out origin/main in $release..."
        git -C "$release" checkout --quiet --detach --force origin/main
    fi

    for name in $SHARED_DIRS; do
        link_shared_dir "$release" "$name"
    done
}

# Render a colour's systemd unit from the template in its release checkout
render_unit() {
    local colour=$1
    local release
    release=$(release_dir "$colour")
    sed -e "s|__COLOUR__|$colour|g" \
        -e "s|__PORT__|$(get_port "$colour")|g" \
        -e "s|__RELEASE_DIR__|$release|g" \
        -e "s|__APP_DIR__|$APP_DIR|g" \
        "$release/$UNIT_TEMPLATE"
}

# Install a colour's rendered unit under /etc/systemd/system
install_unit() {
    local colour=$1
    local rendered
    rendered=$(mktemp)
    render_unit "$colour" > "$rendered"
    sudo cp "$rendered" "/etc/systemd/system/tarmar-$colour.service"
    rm -f "$rendered"
}

# Install the nightly backup timer from a release checkout (issue #575), the
# same way install_unit installs the gunicorn units. The service runs the main
# checkout's `deploy.sh backup`.
install_backup_timer() {
    local release=$1
    local rendered
    rendered=$(mktemp)
    sed -e "s|__APP_DIR__|$APP_DIR|g" "$release/$BACKUP_UNIT_TEMPLATE" > "$rendered"
    sudo cp "$rendered" /etc/systemd/system/tarmar-backup.service
    rm -f "$rendered"
    sudo cp "$release/$BACKUP_TIMER" "/etc/systemd/system/$BACKUP_TIMER"
}

# pg_dump -Fc the database into var/backups/ and keep the newest $BACKUP_KEEP
# of that kind (issue #575). Deploy and nightly backups are pruned separately,
# so a busy day of deploys never pushes the nightly history out. Written to a
# .partial name first, so a failed or interrupted dump is never mistaken for a
# good one. Needs DATABASE_URL from load_env_file.
backup_database() {
    local kind=$1
    local stamp
    local file
    local old
    [ -n "${DATABASE_URL:-}" ] || error "DATABASE_URL is not set; cannot back up"
    mkdir -p "$BACKUP_DIR"
    chmod 700 "$BACKUP_DIR"
    stamp=$(date -u +%Y%m%dT%H%M%SZ)
    file="$BACKUP_DIR/$kind-$stamp.dump"
    log "Backing up the database to $file..."
    if ! pg_dump -Fc --file="$file.partial" --dbname="$DATABASE_URL"; then
        rm -f "$file.partial"
        error "pg_dump failed; no backup taken"
    fi
    mv "$file.partial" "$file"
    # A dump that was written is not yet a dump that can be read (issue #666).
    # pg_restore --list parses the archive's table of contents, which costs
    # milliseconds and catches a truncated or version-mismatched file now,
    # rather than at the moment it is needed. The unreadable file is kept for
    # inspection under a name the pruning below and any restore will skip.
    if ! pg_restore --list "$file" > /dev/null; then
        mv "$file" "$file.unreadable"
        error "pg_dump wrote $file but pg_restore cannot read it; kept as $file.unreadable"
    fi
    ls -1t "$BACKUP_DIR"/"$kind"-*.dump | tail -n +$((BACKUP_KEEP + 1)) | while IFS= read -r old; do
        rm -f -- "$old"
    done
}

# The same connection as $1, against the database named $2. Used only for the
# throwaway database the restore check builds: it keeps the host, port,
# credentials and any query string (sslmode) and replaces the database name.
scratch_database_url() {
    local url=$1
    local name=$2
    local base=${url%%\?*}
    local query=""
    [ "$base" != "$url" ] && query="?${url#*\?}"
    printf '%s/%s%s\n' "${base%/*}" "$name" "$query"
}

# Drop the restore check's scratch database. Run from verify_latest_backup's
# EXIT trap as well as at its end, so a failure to drop is a warning: the check
# has already decided whether the backup restores.
drop_scratch_database() {
    dropdb --maintenance-db="$RESTORE_CHECK_DATABASE_URL" --if-exists "$1" \
        || warn "could not drop the scratch database $1"
}

# Restore the newest dump into a throwaway database and read a table back out
# of it (issue #666). Until this existed, nothing had ever restored a backup
# or checked that it could be: the nightly timer had run since #575 and its
# output had never been read back, so a dump taken by a pg_dump older than the
# server, or written to a disk that filled, would have been discovered
# unrestorable at the one moment that is too late. The nightly run does this
# after taking its dump (`deploy.sh backup --verify`).
#
# It runs as its own role, from RESTORE_CHECK_DATABASE_URL in
# .env.restore-check, not as the app's role in DATABASE_URL (issue #913).
# Restoring needs a database to restore into, so that role holds CREATEDB and
# the app's role does not need it. The restore-check role owns the scratch
# databases it makes and nothing else. Its URL lives in a file of its own,
# read here and nowhere else, because the units load .env: kept there, the
# running app could have read it and created databases with it. pg_dump,
# above, still reads as the app's role. DEPLOY.md says how to create both.
#
# createdb and dropdb take a database *name*, not a connection string: handed
# a URL, they connect over the local socket as the unix user, which is how
# every nightly check failed with `role "sam" does not exist`. They reach the
# server through --maintenance-db, which does accept a URL; pg_restore and psql
# take the scratch URL directly.
#
# Once the scratch database exists, an EXIT trap drops it, so a failed restore,
# a failed count or a SIGTERM from systemd mid-restore cannot leave one behind.
verify_latest_backup() {
    local newest scratch_name scratch_url migration_count
    [ -f "$RESTORE_CHECK_ENV_FILE" ] \
        || error "$RESTORE_CHECK_ENV_FILE is missing; cannot verify a backup (create the restore-check role as DEPLOY.md describes)"
    # The file is the one source: a value inherited from the environment must
    # not stand in for a line the file lacks.
    unset RESTORE_CHECK_DATABASE_URL
    load_env_file "$RESTORE_CHECK_ENV_FILE"
    [ -n "${RESTORE_CHECK_DATABASE_URL:-}" ] \
        || error "RESTORE_CHECK_DATABASE_URL is not set in $RESTORE_CHECK_ENV_FILE; cannot verify a backup (see DEPLOY.md)"
    newest=$(ls -1t "$BACKUP_DIR"/*.dump 2>/dev/null | head -n 1)
    [ -n "$newest" ] || error "no backup to verify in $BACKUP_DIR"
    scratch_name="tarmar_restore_check_$$"
    scratch_url=$(scratch_database_url "$RESTORE_CHECK_DATABASE_URL" "$scratch_name")

    log "Restoring $newest into $scratch_name to check it can be restored..."
    createdb --maintenance-db="$RESTORE_CHECK_DATABASE_URL" "$scratch_name" \
        || error "could not create the scratch database $scratch_name as the role in RESTORE_CHECK_DATABASE_URL (it needs CREATEDB; see DEPLOY.md)"
    # scratch_name is a fixed prefix and a PID, so it is safe inside the string.
    trap "drop_scratch_database $scratch_name" EXIT
    pg_restore --no-owner --no-privileges --dbname="$scratch_url" "$newest" \
        || error "restoring $newest failed: the newest backup cannot be restored"
    migration_count=$(psql --tuples-only --no-align --dbname="$scratch_url" \
        --command="SELECT count(*) FROM django_migrations") \
        || error "restored $newest but could not read django_migrations back from it"
    trap - EXIT
    drop_scratch_database "$scratch_name"

    case "$migration_count" in
        ""|*[!0-9]*) error "restored $newest but django_migrations answered '$migration_count'" ;;
    esac
    [ "$migration_count" -gt 0 ] \
        || error "restored $newest into an empty schema: django_migrations has no rows"
    log "Restored $newest: django_migrations holds $migration_count rows"
}

# Can $1's code take the database as it now stands? Returns 0 when its
# revision has every migration already applied, 1 when it does not, 2 when the
# comparison could not be made at all, and leaves what is missing in
# RELEASE_MIGRATION_GAP. scripts/rollback_migrations.py runs under that
# colour's own Django and venv, fed from the main checkout, so it works on a
# standby from before the script existed (issue #575).
#
# This never exits: both callers decide for themselves. `rollback` refuses;
# a deploy whose switch went bad weighs it against leaving a failing colour
# live (issue #652).
RELEASE_MIGRATION_GAP=""

release_knows_every_migration() {
    local colour=$1
    local release
    release=$(release_dir "$colour")
    RELEASE_MIGRATION_GAP=""
    if [ ! -e "$release/.git" ]; then
        RELEASE_MIGRATION_GAP="no release checkout for $colour at $release"
        return 2
    fi
    load_env_file "$ENV_FILE"
    local check_exit=0
    RELEASE_MIGRATION_GAP=$(cd "$release" && uv run python - < "$APP_DIR/scripts/rollback_migrations.py") \
        || check_exit=$?
    return "$check_exit"
}

# Refuse to switch traffic to a colour whose code lacks migrations already
# applied to the shared database (issue #575): it would run against a schema
# it has never seen.
check_rollback_migrations() {
    local colour=$1
    log "Checking $colour has every migration already applied to the database..."
    local check_exit=0
    release_knows_every_migration "$colour" || check_exit=$?
    if [ "$check_exit" -eq 1 ]; then
        error "refusing to roll back to $colour: the database has migrations its revision ($(release_revision "$colour")) does not have:
$RELEASE_MIGRATION_GAP
Rolling back would run that code against a schema it was not written for. Deploy a fix forward instead."
    elif [ "$check_exit" -ne 0 ]; then
        error "refusing to roll back to $colour: could not compare its migrations with the database (exit $check_exit, output above)"
    fi
}

# Confirm systemd is actually holding the gunicorn worker settings this
# checkout's template asks for (issue #361). The GM's SSE endpoint only
# survives a slow model under gthread with a thread count; reading the flags
# back from `systemctl cat` -- systemd's own copy, after daemon-reload --
# catches the case where the unit on disk was never refreshed and the colour
# is still running the previous ExecStart.
WORKER_FLAGS="--worker-class --threads --timeout"

assert_worker_flags() {
    local colour=$1
    local unit
    log "Checking tarmar-$colour is running with the current worker settings..."
    unit=$(systemctl cat "tarmar-$colour") \
        || error "could not read the tarmar-$colour unit back from systemd"
    for flag in $WORKER_FLAGS; do
        echo "$unit" | grep -q -- "$flag" \
            || error "tarmar-$colour has no $flag; systemd is running a stale unit"
    done
}

# The public hostname the units answer to: the first of .env's ALLOWED_HOSTS.
# A check that talks to a colour's own port has to present it as nginx does,
# or production's ALLOWED_HOSTS answers 400 before a page is rendered.
public_host() {
    local first=${ALLOWED_HOSTS%%,*}
    [ -n "$first" ] || error "ALLOWED_HOSTS is not set in $ENV_FILE; cannot verify a colour by its port"
    echo "$first"
}

# Render the pages of a colour that is not live yet (issue #652). This is the
# check that catches a missing staticfiles manifest, a broken base template or
# a 500 on every page: health_check.py only asks for /health/, which is a
# JsonResponse doing SELECT 1, and for redirects SecurityMiddleware answers
# before URL resolution, sessions, templates or the staticfiles manifest are
# touched. Running it here, against the standby's own gunicorn port, is what
# makes the switch conditional on a release that actually works.
verify_release() {
    local colour=$1
    local port
    port=$(get_port "$colour")
    log "Verifying the $colour release on port $port, before any traffic moves..."
    uv run python scripts/verify_deployment.py "http://127.0.0.1:$port" \
        --as-proxied-host "$(public_host)"
}

# The live site failed verification after traffic had already moved to
# $1 (issue #652). This used to be a bare command under `set -e`: the script
# died with no [ERROR] line, no rollback and no instruction to run one, with
# the broken colour serving. Put $2 back when its code can take the database
# as it now stands; when it cannot -- a migration this deploy applied is the
# usual reason -- say so and leave $1 serving, because old code on a new
# schema is the worse of the two.
recover_from_failed_switch() {
    local failed=$1
    local previous=$2
    warn "$failed failed verification after traffic was switched to it"
    local gap_exit=0
    release_knows_every_migration "$previous" || gap_exit=$?
    if [ "$gap_exit" -eq 0 ]; then
        switch_to "$previous"
        error "rolled back: traffic is on $previous again. $failed is left running on its own port for inspection; fix it and deploy again."
    fi
    error "$failed is live and failing verification, and $previous cannot be switched back to:
$RELEASE_MIGRATION_GAP
Traffic stays on $failed. Fix forward and deploy again, or restore a backup from $BACKUP_DIR before rolling back."
}

# Verify an environment works with exhaustive health checks
verify_environment() {
    local env=$1
    local port=$(get_port "$env")
    local max_attempts=10
    local attempt=1

    log "Waiting for $env environment to start on port $port..."

    # Wait for the server to accept connections
    while [ $attempt -le $max_attempts ]; do
        if curl -sf "http://127.0.0.1:$port/" > /dev/null 2>&1; then
            break
        fi
        log "Attempt $attempt/$max_attempts - waiting..."
        sleep 2
        attempt=$((attempt + 1))
    done

    if [ $attempt -gt $max_attempts ]; then
        error "$env environment failed to start on port $port"
    fi

    # Run exhaustive health checks
    log "Running exhaustive health checks on $env..."
    if uv run python scripts/health_check.py "$port"; then
        log "$env environment passed all health checks!"
        return 0
    else
        error "$env environment failed health checks"
    fi
}

# Switch nginx to point to the specified environment
switch_to() {
    local env=$1
    local port=$(get_port "$env")

    log "Switching traffic to $env (port $port)..."

    # Update upstream config
    sudo tee "$UPSTREAM_CONF" > /dev/null << EOF_UPSTREAM
# Active backend for blue-green deployment
# This file is managed by the deploy script - do not edit manually
# Current: $env (port $port)
upstream tarmar_backend {
    server 127.0.0.1:$port;
}
EOF_UPSTREAM

    # Reload nginx
    sudo nginx -t || error "Nginx config test failed!"
    sudo systemctl reload nginx

    # Update state file
    echo "$env" > "$STATE_FILE"

    log "Traffic now routed to $env"
}

# Main deployment logic
# Warm Ollama's prompt cache for the GM system prompt (issue #330). A deploy
# may change the prompt text, and until one conversation has run past the MLX
# runner's restore point every fresh GM session pays a ~30 s prefill. Same
# non-fatal shape as build_gm_index: Ollama lives on another host and may be
# down; the GM still works, its first turn is just slow.
#
# warm_gm also states the GM prompt against its share of GM_NUM_CTX (issue
# #907). A prompt over that ceiling is not a warm-up failure and the command
# exits 0, so its "OVER the ceiling" line is raised to a deploy warning here
# rather than left in the middle of the warm-up output.
warm_gm_prompt_cache() {
    local output_file
    local warm_status
    output_file=$(mktemp)
    uv run python manage.py warm_gm 2>&1 | tee "$output_file"
    warm_status=${PIPESTATUS[0]}
    if [ "$warm_status" -ne 0 ]; then
        warn "WARNING: GM warm-up failed; first GM turn will be slow"
    fi
    if grep -q "OVER the ceiling" "$output_file"; then
        warn "WARNING: GM system prompt is OVER its share of GM_NUM_CTX (issue #907): $(grep "OVER the ceiling" "$output_file" | head -n 1)"
    fi
    rm -f "$output_file"
}

main() {
    local force=false expected=""
    if [ "${1:-}" = "--force" ]; then
        force=true
    elif [ "${1:-}" = "--expect" ]; then
        expected="${2:?--expect needs the commit the gate recorded}"
    fi

    # Check for changes
    log "Fetching from origin..."
    git fetch

    LOCAL=$(git rev-parse HEAD)
    REMOTE=$(git rev-parse origin/main)

    stand_down_if_main_moved "$expected" "$REMOTE"

    # Before anything is pulled or built: has anyone run the suites on this
    # commit (issue #576)?
    if [ "$force" = true ]; then
        warn "--force: not checking whether $REMOTE has a passing gate record"
    else
        require_gate_record "$REMOTE"
    fi

    if [ "$LOCAL" != "$REMOTE" ]; then
        log "Changes detected!"
        echo "  Local:  $LOCAL"
        echo "  Remote: $REMOTE"
        echo ""

        log "Pulling changes..."
        git pull --ff-only
        # bash parsed this file before the pull replaced it, so carrying on
        # here would run the previous revision's script (issue #328). Hand
        # over to the copy just pulled; it sees no further changes and
        # deploys because of the marker.
        log "Re-running the freshly pulled deploy.sh..."
        exec env TARMAR_DEPLOY_PULLED=1 "$0" "$@"
    fi

    # The canon is synced whether or not this repository changed: a canon
    # change alone is a reason to deploy (issue #921).
    sync_canon
    if [ "$force" = false ] && [ -z "${TARMAR_DEPLOY_PULLED:-}" ] \
        && [ "$CANON_MOVED" = false ]; then
        log "Already up to date. Use --force to redeploy anyway."
        exit 0
    fi
    require_canon_dir

    local active=$(get_active)
    local inactive=$(get_inactive)
    local release
    release=$(release_dir "$inactive")

    log "Current active: $active"
    log "Will deploy to: $inactive ($release)"
    echo ""

    prepare_release "$inactive"
    cd "$release"

    # Sync dependencies
    # Include the cli group so the pyright verification below can resolve
    # cli/ imports (e.g. rich); pyrightconfig.json type-checks cli/.
    log "Syncing dependencies..."
    uv sync --group prod --group cli

    # Run quick verification (lint, types, tests)
    log "Running verification checks..."
    uv run ruff check .
    uv run ruff format --check .
    uv run pyright
    uv run pytest -q
    # A model change with no migration would deploy code whose schema never
    # gets created (issue #575). DEBUG=true only lets settings boot without the
    # production .env, which this step must not see.
    DEBUG=true uv run python manage.py makemigrations --check --dry-run \
        || error "models have changes with no migration; run makemigrations"
    # Guides must be current and free of player-facing deep-secret leaks.
    uv run python scripts/build_guides.py --check
    log "All checks passed!"
    echo ""

    # Load .env for the management commands below. settings.py hard-fails without
    # SECRET_KEY when DEBUG=False, and the test step above must NOT see this env
    # (tests rely on their own controlled settings). DATABASE_URL comes from
    # .env and nowhere else, so `migrate` targets the database the units serve
    # from (issue #574).
    log "Loading .env for management commands..."
    load_env_file "$ENV_FILE"
    [ -n "${DATABASE_URL:-}" ] || error "DATABASE_URL is not set in $ENV_FILE"

    # Collect static files
    log "Collecting static files..."
    uv run python manage.py collectstatic --noinput

    # Back up right before migrating (issue #575). A failed dump stops the
    # deploy: a migration is the one step here that rollback cannot undo.
    backup_database deploy

    # Run migrations
    log "Running migrations..."
    uv run python manage.py migrate

    # Build the GM's canon retrieval index (issue #325). It embeds the corpus
    # through Ollama, which lives on another host and may be down when a deploy
    # runs, so a failure is loud but not fatal: the GM answers without excerpts
    # (gm.retrieval logs a warning per turn) until the next successful build.
    log "Building GM retrieval index..."
    if ! uv run python manage.py build_gm_index; then
        warn "WARNING: GM index build failed; GM will run without retrieval"
    fi

    # Deploy to inactive environment
    log "Deploying to $inactive environment..."
    install_unit "$inactive"
    install_backup_timer "$release"
    sudo systemctl daemon-reload
    sudo systemctl enable --now "$BACKUP_TIMER"
    sudo systemctl restart "tarmar-$inactive"
    assert_worker_flags "$inactive"

    # Wait and verify inactive environment
    sleep 3
    verify_environment "$inactive"
    verify_release "$inactive" \
        || error "$inactive failed verification; traffic stays on $active and nothing was switched"

    # Switch traffic
    switch_to "$inactive"

    # Verify production, through nginx this time, and act on a failure
    log "Verifying production deployment..."
    if ! uv run python scripts/verify_deployment.py; then
        recover_from_failed_switch "$inactive" "$active"
    fi

    # Warm Ollama's prompt cache for the GM system prompt (issue #330).
    log "Warming GM prompt cache..."
    warm_gm_prompt_cache

    # The canon this deploy put live, for the next one to compare against.
    echo "$CANON_HEAD" > "$CANON_STATE_FILE"

    echo ""
    log "Deployment complete!"
    log "Active environment: $inactive"
    log "Rollback available: $active (run: ./deploy.sh rollback)"
}

# Rollback to previous environment
rollback() {
    local active=$(get_active)
    local inactive=$(get_inactive)

    log "Rolling back from $active to $inactive..."

    check_rollback_migrations "$inactive"

    # Verify the old environment is still running
    verify_environment "$inactive"

    # Switch traffic
    switch_to "$inactive"

    log "Rollback complete! Now running on $inactive"
}

# What a colour's release checkout is at, for status
release_revision() {
    local release
    release=$(release_dir "$1")
    if [ -e "$release/.git" ]; then
        git -C "$release" log -1 --format='%h %s'
    else
        echo "(no release checkout)"
    fi
}

# Show current status
status() {
    local active=$(get_active)
    local inactive=$(get_inactive)
    local active_port=$(get_port "$active")
    local inactive_port=$(get_port "$inactive")

    echo "Blue-Green Deployment Status"
    echo "============================"
    echo ""
    echo "Active:   $active (port $active_port) $(release_revision "$active")"
    echo "Standby:  $inactive (port $inactive_port) $(release_revision "$inactive")"
    echo ""
    echo "Service status:"
    systemctl is-active "tarmar-blue" 2>/dev/null && echo "  tarmar-blue: running" || echo "  tarmar-blue: stopped"
    systemctl is-active "tarmar-green" 2>/dev/null && echo "  tarmar-green: running" || echo "  tarmar-green: stopped"
}

# Parse command (only when executed, not when sourced by setup-blue-green.sh)
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    case "${1:-}" in
        rollback)
            take_deploy_lock
            rollback
            ;;
        status)
            status
            ;;
        backup)
            # pg_dump holds ACCESS SHARE on every table for the whole dump,
            # and an ALTER TABLE waiting on ACCESS EXCLUSIVE queues ahead of
            # new readers while it waits -- so a migrate that starts during
            # the nightly dump stalls, and the live site's queries stall
            # behind it, for as long as the dump runs. A nightly backup
            # skipped because a deploy is running is harmless (Persistent=true
            # reruns it at the next boot, and the deploy takes its own dump
            # anyway); a migration stalled behind a dump is not. Issue #667.
            take_deploy_lock
            load_env_file "$ENV_FILE"
            backup_database nightly
            if [ "${2:-}" = "--verify" ]; then
                verify_latest_backup
            fi
            ;;
        *)
            take_deploy_lock
            main "$@"
            ;;
    esac
fi
