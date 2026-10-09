#!/usr/bin/env python
"""
Health check for blue-green deployment.

Tests a specific port to verify the environment is functional:
- /health/ endpoint responds (verifies Django + database connectivity)
- Other pages return 301 (SSL redirect working in production)

Usage:
    python scripts/health_check.py [port]
    python scripts/health_check.py 9066  # Check blue
    python scripts/health_check.py 9067  # Check green
"""

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Run from anywhere: put the repo root on sys.path before importing from it.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.public_paths import LOGIN_PATH  # noqa: E402


class RedirectNotFollowedHandler(urllib.request.HTTPRedirectHandler):
    """Decline to follow redirects (for health checks behind SSL redirect).

    Returning ``None`` leaves urllib to report the redirect response itself.
    ``verify_deployment.RedirectAsErrorHandler`` raises the redirect instead.
    """

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        return None


def make_opener():
    """Create an opener that doesn't follow redirects."""
    return urllib.request.build_opener(RedirectNotFollowedHandler())


def check_health_endpoint(opener: urllib.request.OpenerDirector, base_url: str) -> bool:
    """Check the /health/ endpoint which verifies Django + DB."""
    url = f"{base_url}/health/"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "HealthCheck/1.0"})
        response = opener.open(req, timeout=10)
        content = response.read().decode("utf-8")
        data = json.loads(content)

        if response.status == 200 and data.get("status") == "ok":
            print("  OK: Health endpoint (Django + DB connected)")
            return True
        else:
            print(f"  FAIL: Health endpoint - unexpected response: {content}")
            return False
    except Exception as e:
        print(f"  FAIL: Health endpoint - Error: {e}")
        return False


def check_ssl_redirect(
    opener: urllib.request.OpenerDirector, url: str, description: str
) -> bool:
    """Check that a page returns 301 (SSL redirect working)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "HealthCheck/1.0"})
        opener.open(req, timeout=10)
        # A plain 200 here means SECURE_SSL_REDIRECT is not in force, which on
        # this host means the release is running with DEBUG on or with its
        # production settings block skipped. That used to pass with a WARN
        # ("still consider it OK - server is responding"), which left this
        # check with no failing case short of a connection error or a 4xx/5xx
        # -- part of why a broken release could be switched to (issue #652).
        print(f"  FAIL: {description} - expected a 301 redirect, got 200")
        return False
    except urllib.error.HTTPError as e:
        if e.code in (301, 302):
            print(f"  OK: {description} (SSL redirect working)")
            return True
        else:
            print(f"  FAIL: {description} - HTTP {e.code}")
            return False
    except Exception as e:
        print(f"  FAIL: {description} - Error: {e}")
        return False


def run_health_check(port: int) -> bool:
    """Run health checks against the specified port."""
    base_url = f"http://127.0.0.1:{port}"
    opener = make_opener()
    all_passed = True

    print(f"Running health checks on port {port}...")
    print("")

    # Primary check: /health/ endpoint (Django + DB)
    if not check_health_endpoint(opener, base_url):
        all_passed = False

    # Secondary checks: verify SSL redirect is working
    if not check_ssl_redirect(opener, f"{base_url}/", "Home page SSL redirect"):
        all_passed = False

    if not check_ssl_redirect(
        opener, f"{base_url}{LOGIN_PATH}", "Login page SSL redirect"
    ):
        all_passed = False

    print("")
    if all_passed:
        print(f"SUCCESS: All health checks passed on port {port}")
    else:
        print(f"FAILED: Some health checks failed on port {port}")

    return all_passed


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9066
    return 0 if run_health_check(port) else 1


if __name__ == "__main__":
    sys.exit(main())
