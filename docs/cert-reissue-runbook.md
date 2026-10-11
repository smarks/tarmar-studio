# Runbook: TLS certificates on the deploy host

**Audience:** a Claude Code agent (or human) running **on the deploy host**, where
the repo lives at `/home/sam/dev/tarmar-studio` and you have `sudo`. None of this
can be done from a dev laptop — it needs the server's certbot, nginx, and
Let's Encrypt account.

## The pillars → tarmar reissue is done

This file used to be the plan for issues #30 and #12: move the
`tarmar.duckdns.org` HTTPS block off the old `pillars.duckdns.org` certificate.
That happened in [#272](https://github.com/smarks/tarmar-studio-private/pull/272)
(commit `ff510fa`, "Load the duckdns cert from its own lineage, not pillars'"),
and both issues were closed on 2026-09-04. `nginx/tarmar.conf` has pointed at
`/etc/letsencrypt/live/tarmar.duckdns.org/` ever since. There is nothing left to
carry out; what follows is the general procedure, for the next time a
certificate on this host has to be issued or repointed.

## What nginx serves today

`nginx/tarmar.conf` has two HTTPS server blocks, each on its own certbot
lineage:

| Server names | Certificate lineage |
|---|---|
| `tarmar.origamisoftware.com`, `melee.origamisoftware.com` | `/etc/letsencrypt/live/origamisoftware.com/` (multi-SAN; melee added in #267) |
| `tarmar.duckdns.org` | `/etc/letsencrypt/live/tarmar.duckdns.org/` |

The file in the repo is the source; the live copy is
`/etc/nginx/sites-available/tarmar`, and step 4 below is what puts one in the
other. `tarmar_studio/tests/test_cert_runbook.py` holds this table to
`nginx/tarmar.conf`, so a lineage that changes there and not here fails the
suite rather than leaving this page describing a host that has moved on.

## Preconditions for issuing anything

- The name resolves to this host (for `tarmar.duckdns.org`, the DuckDNS A
  record is current).
- Port 80 is reachable from the internet (Let's Encrypt HTTP-01 challenge).
- The site is currently up on HTTPS, so you can tell if you break it.

## Step 1 — Look at what certbot already has

```bash
sudo certbot certificates          # every lineage, its names and expiry
```

If the name you need is already a SAN on a lineage whose **directory name** you
are happy with, there is nothing to issue: go to step 3.

## Step 2 — Issue a certificate

```bash
sudo certbot certonly --nginx -d <name>
```

This writes `/etc/letsencrypt/live/<name>/{fullchain,privkey}.pem`. If the
`--nginx` HTTP-01 challenge fails (port 80, redirects), fall back to the
webroot nginx already serves:

```bash
sudo certbot certonly --webroot -w /home/sam/dev/tarmar-studio/staticfiles -d <name>
```

Confirm the live directory exists before touching nginx:

```bash
sudo ls -l /etc/letsencrypt/live/<name>/
```

## Step 3 — Point nginx at it

Edit the `ssl_certificate` / `ssl_certificate_key` lines of the right server
block in `/home/sam/dev/tarmar-studio/nginx/tarmar.conf` — and update the table
above in the same commit, or the test named there fails.

## Step 4 — Test and reload

```bash
sudo cp /home/sam/dev/tarmar-studio/nginx/tarmar.conf /etc/nginx/sites-available/tarmar
sudo nginx -t && sudo systemctl reload nginx
```

If `nginx -t` fails, **do not reload** — fix the config first.

## Step 5 — Verify

```bash
curl -sI https://<name>/ | head -1              # expect 200/301/302, not a TLS error
echo | openssl s_client -servername <name> -connect <name>:443 2>/dev/null \
    | openssl x509 -noout -issuer -subject -dates
sudo certbot renew --dry-run                    # every lineage still renews
```

Also load the site in a browser and confirm no certificate warning.

## Rollback

Put the previous paths back and reload:

```bash
sudoedit /etc/nginx/sites-available/tarmar     # restore the old ssl_certificate lines
sudo nginx -t && sudo systemctl reload nginx
```

Do **not** `certbot delete` the lineage you moved off until you are confident —
it is the rollback target. Retiring an old lineage is a separate, deliberate
step, taken days later and never in the same session as the switch.

## Step 6 — Commit

The nginx config is tracked, so dev and prod only agree once it is committed:

```bash
cd /home/sam/dev/tarmar-studio
git add nginx/tarmar.conf docs/cert-reissue-runbook.md
git commit -m "Point <name> at its own Let's Encrypt lineage"
git push origin main
```
