#!/bin/bash
# One-time setup for blue-green deployment.
#
# Run once, as sam (it uses sudo for the root-owned pieces, as deploy.sh does):
#   ./setup-blue-green.sh
#
# This will:
#   1. Create the blue and green release checkouts
#   2. Render and install both systemd units
#   3. Install the nginx upstream and site configs
#   4. Run a first full deploy so blue is built, verified and live

set -e

# deploy.sh holds the helpers; it only dispatches a command when run directly.
source "$(dirname "$0")/deploy.sh"
take_deploy_lock

log "Setting up blue-green deployment..."

for colour in blue green; do
    prepare_release "$colour"
    install_unit "$colour"
done
sudo systemctl daemon-reload
sudo systemctl enable tarmar-blue tarmar-green

# Stop old tarmar service if running
if systemctl is-active tarmar >/dev/null 2>&1; then
    log "Stopping old tarmar service..."
    sudo systemctl stop tarmar
    sudo systemctl disable tarmar
fi

log "Installing nginx configs..."
sudo cp nginx/tarmar-upstream.conf /etc/nginx/conf.d/
sudo cp nginx/tarmar.conf /etc/nginx/sites-available/tarmar
sudo nginx -t
sudo systemctl reload nginx

# Start from green as "active" so the first deploy builds and verifies blue
# the normal way, then switches traffic to it.
echo "green" > "$STATE_FILE"
main --force

log "Blue-green deployment setup complete!"
status
