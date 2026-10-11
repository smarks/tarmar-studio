#!/bin/bash
# What a CI job on datahorde may not reach (issue #964). Run as root by
# tarmar-ci-runner.service before each start, and by install.sh.
#
# Datahorde is the production host, and some of what it serves listens on its
# LAN address, not only on loopback: production Postgres answers on
# 192.168.4.163:5432. The container's own network namespace keeps it off the
# host's loopback; these rules close the rest:
#
#   - anything the CI bridge sends to the host itself, on any address;
#   - anything it would send on to a private network (the LAN, where the Mac
#     Studio's Ollama listens, and Tailscale);
#   - all IPv6 from the bridge.
#
# What is left is the public internet over IPv4, which a job needs for
# GitHub, PyPI and npm.
#
# The rules live in an nftables table of their own, inet tarmar_ci. ufw and
# Docker manage their own iptables chains and flush them on reload; they do not
# touch this table. In nftables a drop in any base chain is final whatever
# other tables accept, so nothing they add can open the wall. The table is
# replaced whole on each run, so a restart does not stack copies. A full
# `flush ruleset` (`nft -f /etc/nftables.conf`, or enabling nftables.service)
# would remove it; jit-config.sh refuses to start a job while it is missing.

set -euo pipefail

# Must match the network install.sh creates.
CI_BRIDGE="br-tarmar-ci"
PRIVATE_NETWORKS="10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10"

# One transaction: declare the table (a no-op if it exists), delete it, and
# define it again, so there is no moment with no wall.
nft -f - <<NFT
table inet tarmar_ci
delete table inet tarmar_ci
table inet tarmar_ci {
    chain input {
        type filter hook input priority 0; policy accept;
        iifname "${CI_BRIDGE}" drop
    }
    chain forward {
        type filter hook forward priority 0; policy accept;
        iifname "${CI_BRIDGE}" meta nfproto ipv6 drop
        iifname "${CI_BRIDGE}" ip daddr { ${PRIVATE_NETWORKS} } drop
    }
}
NFT

# No IPv6 on the bridge at all, link-local included, once it exists.
if [ -e "/proc/sys/net/ipv6/conf/${CI_BRIDGE}/disable_ipv6" ]; then
    echo 1 > "/proc/sys/net/ipv6/conf/${CI_BRIDGE}/disable_ipv6"
fi
