#!/bin/bash
# Require the CI check before anything merges to main (issue #576).
#
# Run as the account that is logged in to gh, once CI has produced a green run:
#
#     scripts/ci-runner/protect-main.sh
#
# It requires the `test` job of ci.yml (the status check GitHub names after
# the job's `name:`) to pass, from GitHub Actions and no other app, and refuses
# force-pushes and deletion of main. Admins are held to it too: everyone who
# pushes here is the owner, so an admin exemption would exempt everyone. It
# does not require reviews: the pair-programming rule in the house CLAUDE.md
# is the review gate, and a required-review rule would block the merges that
# rule already covers. No root needed.

set -euo pipefail

# REPOSITORY (issue #961). Protection suits a public repository, so there is no
# visibility check here.
# shellcheck source=repository.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/repository.sh"
BRANCH="main"
# The status check name of the `test` job in .github/workflows/ci.yml;
# scripts/test_ci_runner.py checks that it is the name the workflow produces.
# Issue #920 required two, one per canon; #921 left the default canon's alone.
REQUIRED_CHECK="test (default canon)"
# GitHub Actions' app id, so a status of one of those names posted by anything
# else does not satisfy the rule.
GITHUB_ACTIONS_APP_ID="15368"

gh api -X PUT "repos/${REPOSITORY}/branches/${BRANCH}/protection" \
    --input - <<JSON
{
  "required_status_checks": {
    "strict": true,
    "checks": [
      {"context": "${REQUIRED_CHECK}", "app_id": ${GITHUB_ACTIONS_APP_ID}}
    ]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON

echo "main now requires the '${REQUIRED_CHECK}' check; force-pushes and deletion refused."
