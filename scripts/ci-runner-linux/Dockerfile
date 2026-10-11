# The GitHub Actions runner that runs CI on datahorde when the repository
# variable CI_RUNNER is "datahorde" (issue #964).
#
# Datahorde is the production host, so the runner lives in a container that
# holds nothing of the host's: no Docker socket, no host files, no host
# network. Each container takes one job and exits (a just-in-time, ephemeral
# registration), so nothing a job leaves behind, credentials included, reaches
# the next. install.sh builds this image; tarmar-ci-runner.service runs it.
#
# The image carries only what a job cannot set up without root: the system
# libraries Chromium and weasyprint load, git for actions/checkout, and gh for
# the notify job. The job installs its own uv, Python, Node and browser
# (ci.yml), as it does on GitHub's machines, so both modes run the same steps.
#
# PLAYWRIGHT_BROWSERS_PATH stays unset: ci.yml caches the browser at
# ~/.cache/ms-playwright, Playwright's default on Linux.

FROM ubuntu:24.04

# The one runner release this image installs. Bump both together: the
# checksum is the SHA256 GitHub publishes in the release notes for the
# linux-x64 tarball. The Mac runner's installer pins the same version.
ARG RUNNER_VERSION=2.337.0
ARG RUNNER_SHA256=70920811a4f8ad4328818682bca5c6469c1c942fab52448868071d0063816613
# The @playwright/test version package-lock.json resolves. Its install-deps
# names the system libraries that version's Chromium needs; rebuild the image
# (install.sh) after a Playwright upgrade.
ARG PLAYWRIGHT_VERSION=1.57.0
# gh, which the notify job's action calls to find the previous run's result.
# The checksum is from the release's gh_<version>_checksums.txt.
ARG GH_VERSION=2.102.0
ARG GH_SHA256=bb766f710eef8ede859c18578c72c327597cd4c8a85b06001b1f3843c6019386
# The job's account: a uid nothing on datahorde uses (1001 there is `guest`),
# so a process that escaped the container would be nobody on the host.
ARG RUNNER_UID=64001

ENV DEBIAN_FRONTEND=noninteractive

# git for actions/checkout; the pango and harfbuzz libraries for weasyprint;
# nodejs and npm only so install-deps can run at build time (the job brings its
# own Node with actions/setup-node).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl git jq tar gzip xz-utils unzip \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core \
        nodejs npm \
    && npx --yes "playwright@${PLAYWRIGHT_VERSION}" install-deps chromium \
    && rm -rf /var/lib/apt/lists/* /root/.npm

RUN curl -fsSL -o gh.tar.gz \
        "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_amd64.tar.gz" \
    && echo "${GH_SHA256}  gh.tar.gz" | sha256sum --check --strict \
    && tar -xzf gh.tar.gz --strip-components=2 -C /usr/local/bin "gh_${GH_VERSION}_linux_amd64/bin/gh" \
    && rm gh.tar.gz

# The job's account. No sudo: nothing a job runs needs root.
RUN useradd --create-home --uid "${RUNNER_UID}" --shell /bin/bash runner

WORKDIR /home/runner/actions-runner
RUN curl -fsSL -o runner.tar.gz \
        "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz" \
    && echo "${RUNNER_SHA256}  runner.tar.gz" | sha256sum --check --strict \
    && tar -xzf runner.tar.gz \
    && rm runner.tar.gz \
    && ./bin/installdependencies.sh \
    && rm -rf /var/lib/apt/lists/* \
    && chown -R runner:runner /home/runner

COPY --chown=runner:runner entrypoint.sh /home/runner/entrypoint.sh

USER runner
ENTRYPOINT ["/home/runner/entrypoint.sh"]
