#!/bin/bash
# Make this Mac ready to serve the Tarmar Studio Games Master LLM, and tell the
# deploy host how to reach it.
#
# Run by the LaunchAgent com.origamisoftware.tarmar-llm-ready at login and every
# 15 minutes after that. Three jobs, in order:
#
#   1. Publish this Mac's current LAN address to the deploy host's /etc/hosts
#      under the stable name "tarmar-llm". This machine takes its address by
#      DHCP with no reservation, so the deploy host's OLLAMA_BASE_URL points at
#      that name rather than a literal address that a new lease would break.
#      This goes first because it needs neither Ollama nor the model, and it is
#      what makes this Mac reachable at all: while it ran after the wait, a new
#      lease taken during an Ollama restart left production resolving
#      tarmar-llm to a dead address until Ollama came back (issue #676).
#   2. Wait for Ollama (started separately by com.origamisoftware.ollama) to
#      answer /api/version, starting that agent if it is silent after 30 s.
#   3. Warm the Games Master model if it is not already resident. Ollama comes
#      up with nothing loaded, so the first request after a reboot pays both a
#      ~60 s model load and a ~7k-token prefill, long enough that the web app
#      reports the Games Master as unreachable. The warming itself is the
#      repo's own "manage.py warm_gm" run over ssh, so the prompt-cache logic
#      lives in exactly one place.
#
# Installed on the Mac Studio as the LaunchAgent beside this file; see the
# README in this directory. Written 2026-09-11 (issue #386) after a reboot
# left the Games Master cold: #330 asked for warming "after each deploy and
# after Ollama restarts" and only the deploy half shipped.

set -u

# Every value here can be overridden from the environment, which is how the
# LaunchAgent plist carries this particular Mac's settings without the script
# having to know which Mac it is on. The defaults are the live ones.
OLLAMA_LOCAL_URL="${OLLAMA_LOCAL_URL:-http://127.0.0.1:11434}"
OLLAMA_AGENT_LABEL="${OLLAMA_AGENT_LABEL:-com.origamisoftware.ollama}"
OLLAMA_AGENT_PLIST="${OLLAMA_AGENT_PLIST:-${HOME}/Library/LaunchAgents/com.origamisoftware.ollama.plist}"
DEPLOY_HOST_SSH="${DEPLOY_HOST_SSH:-sam@192.168.4.163}"
DEPLOY_HOST_CHECKOUT="${DEPLOY_HOST_CHECKOUT:-/home/sam/dev/tarmar-studio}"
DEPLOY_HOST_DATABASE_URL="${DEPLOY_HOST_DATABASE_URL:-postgres://tarmar:tarmar@localhost:5432/tarmar_studio_db}"
DEPLOY_HOST_UV_PATH="${DEPLOY_HOST_UV_PATH:-/home/sam/.local/bin}"
PUBLISHED_HOSTNAME="${PUBLISHED_HOSTNAME:-tarmar-llm}"
NETWORK_INTERFACE="${NETWORK_INTERFACE:-en0}"
# Absolute paths so a stray PATH cannot change what runs; overridable so the
# tests can put stand-ins in their place, like DEPLOY_HOST_UV_PATH.
CURL_BIN="${CURL_BIN:-/usr/bin/curl}"
IPCONFIG_BIN="${IPCONFIG_BIN:-/usr/sbin/ipconfig}"
READINESS_TIMEOUT_SECONDS="${READINESS_TIMEOUT_SECONDS:-600}"
SSH_OPTIONS=(-o ConnectTimeout=10 -o BatchMode=yes -o StrictHostKeyChecking=accept-new)

log() {
    printf '%s tarmar-llm-ready: %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*"
}

# Bring the Ollama agent back however it is currently broken: bootstrap loads it
# when it is not registered at all (someone ran "launchctl bootout", or the login
# load failed), kickstart restarts it when it is registered but wedged. Running
# bootstrap on an already-loaded service is a harmless error, so try both.
start_ollama_agent() {
    local domain="gui/$(id -u)"
    if launchctl bootstrap "$domain" "$OLLAMA_AGENT_PLIST" >/dev/null 2>&1; then
        log "bootstrapped ${OLLAMA_AGENT_LABEL}"
        return 0
    fi
    if launchctl kickstart "${domain}/${OLLAMA_AGENT_LABEL}" >/dev/null 2>&1; then
        log "kickstarted ${OLLAMA_AGENT_LABEL}"
        return 0
    fi
    log "could not start ${OLLAMA_AGENT_LABEL}"
    return 1
}

ollama_is_answering() {
    "$CURL_BIN" -fsS -m 5 "${OLLAMA_LOCAL_URL}/api/version" >/dev/null 2>&1
}

wait_for_ollama() {
    local deadline=$((SECONDS + READINESS_TIMEOUT_SECONDS))
    local have_kickstarted=0
    while [ "$SECONDS" -lt "$deadline" ]; do
        if ollama_is_answering; then
            return 0
        fi
        # Ollama is a separate agent, and if it failed to load there is nobody
        # else to notice. Give it half a minute to come up on its own, then
        # start it once; after that just keep waiting.
        if [ "$have_kickstarted" -eq 0 ] && [ "$SECONDS" -ge 30 ]; then
            log "Ollama silent after 30s; starting ${OLLAMA_AGENT_LABEL}"
            start_ollama_agent
            have_kickstarted=1
        fi
        sleep 5
    done
    return 1
}

publish_address_to_deploy_host() {
    local local_address
    local_address="$("$IPCONFIG_BIN" getifaddr "$NETWORK_INTERFACE")"
    if [ -z "$local_address" ]; then
        # Printed, not logged: main logs every line this function produces.
        echo "no address on ${NETWORK_INTERFACE}"
        return 1
    fi

    # Rewrite the deploy host's /etc/hosts entry only when it is missing or
    # stale, and do it through a temporary file so a failure part-way cannot
    # leave the file truncated.
    ssh "${SSH_OPTIONS[@]}" "$DEPLOY_HOST_SSH" \
        "ADDRESS='${local_address}' NAME='${PUBLISHED_HOSTNAME}' bash -s" <<'REMOTE'
set -eu
current="$(awk -v name="$NAME" '$2 == name { print $1; exit }' /etc/hosts)"
if [ "$current" = "$ADDRESS" ]; then
    echo "hosts entry already ${NAME} -> ${ADDRESS}"
    exit 0
fi
temporary="$(mktemp)"
grep -v -E "[[:space:]]${NAME}([[:space:]]|$)" /etc/hosts > "$temporary"
printf '%s %s\n' "$ADDRESS" "$NAME" >> "$temporary"
sudo cp "$temporary" /etc/hosts
rm -f "$temporary"
echo "hosts entry set ${NAME} -> ${ADDRESS} (was '${current:-unset}')"
REMOTE
}

# Run "manage.py <arguments>" in the deploy host's checkout over ssh, with the
# same PATH and database as the app. The one place that command prefix is
# written; the arguments arrive as a single string for the remote shell.
run_manage_py_on_deploy_host() {
    ssh "${SSH_OPTIONS[@]}" "$DEPLOY_HOST_SSH" \
        "export PATH=${DEPLOY_HOST_UV_PATH}:\$PATH; cd ${DEPLOY_HOST_CHECKOUT} && DATABASE_URL='${DEPLOY_HOST_DATABASE_URL}' uv run python manage.py $1"
}

# Ask the app which model it will use, rather than reading GM_MODEL out of the
# deploy host's .env: that line is optional now that the app's default is the
# model production runs (issue #65), and a script that required it would skip
# the warm-up for good the day someone deleted it. settings.GM_MODEL is what
# warm_gm and every Games Master turn use, with the .env loaded and the default
# applied when it is silent, so there is one answer. A banner or warning ahead
# of it on stdout cannot matter: only the last line is the name. A failing
# command (host unreachable, a traceback) is never read as a name, however much
# it printed first; the reason goes to stderr, which main's caller does not
# capture, so the log says why and not only that.
games_master_model_name() {
    local stderr_file output status=0
    stderr_file="$(mktemp)"
    output="$(run_manage_py_on_deploy_host "shell -c 'from django.conf import settings; print(settings.GM_MODEL)'" 2>"$stderr_file")" || status=$?
    if [ "$status" -ne 0 ]; then
        log "reading the Games Master model failed (exit ${status}): $(tail -n 1 "$stderr_file")" >&2
        rm -f "$stderr_file"
        return 1
    fi
    rm -f "$stderr_file"
    printf '%s' "$output" | tail -n 1 | tr -d '\r\n'
}

model_is_resident() {
    local model_name="$1"
    "$CURL_BIN" -fsS -m 10 "${OLLAMA_LOCAL_URL}/api/ps" 2>/dev/null \
        | /usr/bin/grep -q "\"model\":\"${model_name}\""
}

warm_games_master() {
    run_manage_py_on_deploy_host warm_gm 2>&1
}

main() {
    # Before the wait, and with its outcome read rather than lost to a
    # pipeline (issue #676). `f | while read` takes the while's exit status,
    # so a missing en0 address, or the remote sudo cp failing under set -eu,
    # went by unnoticed and the round carried on reporting success.
    local publication publication_status=0
    publication="$(publish_address_to_deploy_host 2>&1)" || publication_status=$?
    while IFS= read -r line; do
        [ -n "$line" ] && log "$line"
    done <<< "$publication"
    if [ "$publication_status" -ne 0 ]; then
        log "WARNING: could not publish this Mac's address to ${DEPLOY_HOST_SSH} (exit ${publication_status}); ${PUBLISHED_HOSTNAME} may still resolve to an address that is no longer this machine"
    fi

    if ! wait_for_ollama; then
        log "Ollama did not answer within ${READINESS_TIMEOUT_SECONDS}s; giving up this round"
        exit 1
    fi

    local model_name
    model_name="$(games_master_model_name)" || model_name=""
    if [ -z "$model_name" ]; then
        log "could not read the Games Master model from the deploy host; skipping warm-up"
        exit 1
    fi

    if model_is_resident "$model_name"; then
        log "${model_name} already resident; nothing to warm"
        exit 0
    fi

    log "${model_name} not resident; warming"
    warm_games_master | while IFS= read -r line; do log "warm_gm: $line"; done
    if model_is_resident "$model_name"; then
        log "${model_name} resident; Games Master ready"
    else
        log "WARNING: ${model_name} still not resident after warm-up"
        exit 1
    fi
}

main "$@"
