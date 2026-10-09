#!/usr/bin/env python
"""
Deployment verification script.

Verifies that the production site is running correctly by:
1. Checking unauthenticated endpoints (health, static files)
2. Authenticating through the passphrase gate
3. Checking all gate-protected pages render with expected content
4. Logging in as a user and checking authenticated pages render correctly

Catches real issues: missing migrations, broken templates, missing static
files, unclosed HTML tags, blank pages, and 500 errors.

Usage:
    python scripts/verify_deployment.py [base_url] [username] [password]
    python scripts/verify_deployment.py https://tarmar.origamisoftware.com sam mellon
    python scripts/verify_deployment.py http://127.0.0.1:9067 \
        --as-proxied-host tarmar.origamisoftware.com

The third form is how a deploy checks the standby colour before any traffic
moves to it (issue #652): it talks to that colour's own gunicorn port, on
loopback, presenting the request the way nginx would have. See ProxiedOrigin.
"""

import argparse
import http.cookiejar
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

# Run from anywhere: put the repo root on sys.path before importing from it.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common.gate import DEV_GATE_PASSPHRASE  # noqa: E402
from common.public_paths import LOGIN_PATH  # noqa: E402

DEFAULT_URL = "https://tarmar.origamisoftware.com"
# Read from the environment so the gate secret isn't hardcoded in the repo;
# falls back to the shared dev default (common.gate) for local runs.
PASSPHRASE = os.environ.get("GATE_PASSPHRASE", DEV_GATE_PASSPHRASE)


@dataclass(frozen=True)
class ProxiedOrigin:
    """The public origin a request to a colour's own port is pretending to be.

    A colour's gunicorn is bound to loopback and only ever addressed through
    nginx, so a check that talks to its port directly has to present what
    nginx presents, or it is checking a request the site never receives:

    * ``Host``, because production's ``ALLOWED_HOSTS`` does not include
      ``127.0.0.1`` -- without it every page is a 400 before it is rendered.
    * ``X-Forwarded-Proto: https``, because ``SECURE_SSL_REDIRECT`` is on and
      gunicorn turns that header from a trusted proxy into ``wsgi.url_scheme
      = https``. Without it SecurityMiddleware answers 301 before URL
      resolution and nothing below the middleware is ever exercised.

    The Referer sent with a POST is built from this origin too: Django checks
    it against the request's host, which is the one in ``Host``.
    """

    host: str

    @property
    def headers(self) -> dict[str, str]:
        """The headers nginx adds, as this deployment's nginx adds them."""
        return {"Host": self.host, "X-Forwarded-Proto": "https"}


def request_headers(origin: "ProxiedOrigin | None") -> dict[str, str]:
    """Base headers for every request, with the proxy's if there is one."""
    headers = {"User-Agent": "DeploymentVerifier/1.0"}
    if origin:
        headers.update(origin.headers)
    return headers


def referer_for(origin: "ProxiedOrigin | None", base: str, path: str) -> str:
    """The Referer a browser on this deployment would send for ``path``."""
    return f"https://{origin.host}{path}" if origin else f"{base}{path}"


class RedirectAsErrorHandler(urllib.request.HTTPRedirectHandler):
    """Raise every redirect as an HTTPError, so the caller can read its status.

    Unlike ``health_check.RedirectNotFollowedHandler``, which declines to
    follow and leaves urllib to report the redirect response.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


class ProxiedCookiePolicy(http.cookiejar.DefaultCookiePolicy):
    """Return ``Secure`` cookies over the loopback connection they belong to.

    ``SESSION_COOKIE_SECURE`` and ``CSRF_COOKIE_SECURE`` are on in production,
    so the session and CSRF cookies are marked Secure and the default policy
    withholds them from a plain-http request. Talking to a colour's own port
    is plain http, and the request is the https one nginx would have passed
    on -- ``X-Forwarded-Proto`` says so and the app agrees. Without this the
    gate POST looks like it worked (it answers 200), every later page is the
    gate page again, and the check reports a broken site on a healthy
    release. Only used when a proxied origin was asked for.
    """

    def return_ok_secure(self, cookie, request) -> bool:
        return True


def make_session(origin=None):
    """Create an opener with cookie jar that follows redirects."""
    ctx = ssl.create_default_context()
    jar = http.cookiejar.CookieJar(ProxiedCookiePolicy() if origin else None)
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar),
        urllib.request.HTTPSHandler(context=ctx),
    )
    return opener, jar, ctx


def get_csrf(jar):
    """Extract CSRF token from cookies."""
    for c in jar:
        if c.name == "csrftoken":
            return c.value
    return None


def fetch(opener, url, data=None, referer=None, origin=None):
    """Fetch a URL, return (status, body). Follows redirects."""
    headers = request_headers(origin)
    if referer:
        headers["Referer"] = referer
    if data:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        resp = opener.open(req, timeout=15)
        return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return e.code, body


def fetch_no_redirect(jar, ctx, url, origin=None):
    """Fetch without following redirects — returns raw status code."""
    opener = urllib.request.build_opener(
        RedirectAsErrorHandler(),
        urllib.request.HTTPCookieProcessor(jar),
        urllib.request.HTTPSHandler(context=ctx),
    )
    req = urllib.request.Request(url, headers=request_headers(origin))
    try:
        resp = opener.open(req, timeout=15)
        resp.read()
        return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def check(label, ok, detail=""):
    """Print a test result."""
    status = "OK" if ok else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"  {status}: {label}{suffix}")
    return ok


ICON_LINK = re.compile(
    r"""<link[^>]+rel=["']icon["'][^>]+href=["']([^"']+)["']""", re.IGNORECASE
)


def check_rendered_icon(opener, base, origin=None) -> bool:
    """Fetch the icon this release's own pages ask for, and check it is served.

    The static check used to name a hashed file, ``favicon.e2c99b851269.svg``,
    baked into this script (issue #669). nginx serves ``/static/`` from the
    main checkout's ``staticfiles/``, which both colours share and which
    ``collectstatic`` never prunes, so that exact file survived every release
    whether or not the current one collected anything -- the check could not
    fail on the thing it existed to catch. In the other direction, editing
    ``static/images/favicon.svg`` changed the hash and failed the deploy on a
    cosmetic change until somebody edited this line.

    Reading the name out of a page instead ties the check to this release:
    the ``href`` is written by the running code through its own staticfiles
    manifest, and fetching it proves nginx (or whitenoise, on a colour's own
    port) serves the file that manifest names.

    Args:
        opener: The session to fetch with.
        base: The deployment's base URL.
        origin: The proxied origin, when checking a colour by its own port.

    Returns:
        True when a page linked an icon and that icon was served.
    """
    status, body = fetch(opener, f"{base}/tarmar/", origin=origin)
    if status != 200:
        return check("Icon: page to read it from loads", False, f"got {status}")
    match = ICON_LINK.search(body)
    if not match:
        return check("Icon: page links one", False, "no <link rel=icon> in /tarmar/")
    href = match.group(1)
    status, _ = fetch(opener, urllib.parse.urljoin(f"{base}/", href), origin=origin)
    return check(f"Icon {href} is served", status == 200, f"got {status}")


def check_html_basics(body, label):
    """Check that HTML response has basic structure and isn't broken."""
    errors = []
    if not body or len(body.strip()) < 50:
        errors.append("empty or near-empty response")
    if "<title>" in body and "</title>" not in body:
        errors.append("unclosed <title> tag (causes blank page)")
    if "Server Error (500)" in body or "Internal Server Error" in body:
        errors.append("contains 500 error page")
    if "Bad Request (400)" in body:
        errors.append("contains 400 error page")
    if "Missing staticfiles manifest" in body:
        errors.append("missing staticfiles (collectstatic needed)")
    if "does not exist" in body and "relation" in body:
        errors.append("missing database table (migration needed)")
    if errors:
        return check(label, False, "; ".join(errors))
    return True


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    """Read the command line.

    Args:
        argv: Arguments after the program name.

    Returns:
        The parsed arguments: ``base_url``, optional ``username`` and
        ``password``, and optional ``as_proxied_host``.
    """
    parser = argparse.ArgumentParser(description="Verify a Tarmar deployment.")
    parser.add_argument("base_url", nargs="?", default=DEFAULT_URL)
    parser.add_argument("username", nargs="?", default=None)
    parser.add_argument("password", nargs="?", default=None)
    parser.add_argument(
        "--as-proxied-host",
        default=None,
        metavar="HOST",
        help=(
            "present requests the way nginx would, for a base_url that points "
            "straight at a colour's gunicorn port (see ProxiedOrigin)"
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_arguments(sys.argv[1:] if argv is None else argv)
    base = arguments.base_url.rstrip("/")
    username = arguments.username
    password = arguments.password
    origin = (
        ProxiedOrigin(arguments.as_proxied_host) if arguments.as_proxied_host else None
    )

    print(f"Verifying deployment at {base}")
    if origin:
        print(f"  presented as nginx presents it, Host: {origin.host}")
    print()
    failed = 0

    opener, jar, ctx = make_session(origin)

    # ── Unauthenticated tests ──
    print("Unauthenticated:")

    status, body = fetch(opener, f"{base}/health/", origin=origin)
    if not check("GET /health/ returns 200", status == 200, f"got {status}"):
        failed += 1
    elif not check(
        "Health response is JSON with status=ok", '"status"' in body and '"ok"' in body
    ):
        failed += 1

    print()

    # ── Gate authentication ──
    print("Passphrase gate:")

    status, body = fetch(opener, f"{base}/", origin=origin)
    if not check(
        "GET / returns gate page",
        status == 200 and "passphrase" in body,
        f"status={status}, has form={'passphrase' in body}",
    ):
        failed += 1
        print("\n  Cannot proceed without gate page. Aborting.")
        return 1

    if not check_html_basics(body, "Gate page HTML is well-formed"):
        failed += 1

    csrf = get_csrf(jar)
    if not csrf:
        match = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', body)
        csrf = match.group(1) if match else None
    if not check("CSRF token obtained", csrf is not None):
        failed += 1
        return 1

    data = urllib.parse.urlencode(
        {"csrfmiddlewaretoken": csrf, "passphrase": PASSPHRASE}
    ).encode()
    status, body = fetch(
        opener,
        f"{base}/gate/",
        data=data,
        referer=referer_for(origin, base, "/gate/"),
        origin=origin,
    )
    # Not the status alone: a refused passphrase, a CSRF failure and a session
    # cookie that never came back all answer 200 with the gate page again, and
    # every page after this one would then be checked against that page.
    gate_passed = status == 200 and "passphrase" not in body
    if not check(
        "POST /gate/ authenticates",
        gate_passed,
        ""
        if gate_passed
        else (
            f"got {status}, still the gate page" if status == 200 else f"got {status}"
        ),
    ):
        failed += 1
        return 1

    if not check_rendered_icon(opener, base, origin):
        failed += 1

    print()

    # ── Gate-protected pages (no login required) ──
    print("Gate-protected pages (no login):")

    tests = [
        ("/", "Root redirect to Tarmar", ["Tarmar"]),
        ("/tarmar/", "Tarmar home", ["Tarmar"]),
        ("/tarmar/about/", "About page", ["About"]),
        (LOGIN_PATH, "Login page", ["username", "password"]),
        ("/tarmar/accounts/register/", "Registration page", ["Register"]),
        ("/tarmar/reference/", "Rules reference", []),
    ]

    for path, label, content_checks in tests:
        status, body = fetch(opener, f"{base}{path}", origin=origin)
        if not check(f"GET {path} returns 200", status == 200, f"got {status}"):
            failed += 1
            continue
        if not check_html_basics(body, f"{label} HTML"):
            failed += 1
            continue
        for needle in content_checks:
            if needle.lower() not in body.lower():
                check(f"{label} contains '{needle}'", False, "missing expected content")
                failed += 1

    # Pages that should redirect to login (no-redirect check)
    redirect_tests = [
        ("/tarmar/characters/", "Characters"),
        ("/tarmar/gm/", "GM chatbot"),
        ("/tarmar/accounts/profile/", "Profile"),
    ]
    for path, label in redirect_tests:
        status = fetch_no_redirect(jar, ctx, f"{base}{path}", origin=origin)
        if not check(f"GET {path} redirects to login", status == 302, f"got {status}"):
            failed += 1

    # 404 test
    status, _ = fetch(opener, f"{base}/nonexistent-page-12345/", origin=origin)
    if not check("GET /nonexistent returns 404", status == 404, f"got {status}"):
        failed += 1

    print()

    # ── Logged-in tests ──
    if username and password:
        print(f"Authenticated pages (user={username}):")

        # Login
        status, body = fetch(opener, f"{base}{LOGIN_PATH}", origin=origin)
        csrf = get_csrf(jar)
        data = urllib.parse.urlencode(
            {"csrfmiddlewaretoken": csrf, "username": username, "password": password}
        ).encode()
        status, body = fetch(
            opener,
            f"{base}{LOGIN_PATH}",
            data=data,
            referer=referer_for(origin, base, LOGIN_PATH),
            origin=origin,
        )
        # Successful login redirects to home; failed login re-shows login form
        has_login_form = 'name="username"' in body and 'name="password"' in body
        logged_in = status == 200 and not has_login_form
        if not check(
            "Login succeeds",
            logged_in,
            "login form re-displayed (wrong password?)"
            if has_login_form
            else f"status={status}",
        ):
            failed += 1
            print("  Skipping logged-in tests.")
        else:
            auth_tests = [
                ("/tarmar/characters/", "Characters list", ["Characters"]),
                ("/tarmar/characters/create/", "Create character", ["character"]),
                ("/tarmar/gm/", "GM chatbot", ["chat", "Send"]),
                ("/tarmar/accounts/profile/", "Profile page", ["profile"]),
                ("/tarmar/accounts/users/", "Users list", ["Users"]),
            ]
            for path, label, content_checks in auth_tests:
                status, body = fetch(opener, f"{base}{path}", origin=origin)
                if not check(f"GET {path} returns 200", status == 200, f"got {status}"):
                    failed += 1
                    continue
                if not check_html_basics(body, f"{label} HTML"):
                    failed += 1
                    continue
                for needle in content_checks:
                    if needle.lower() not in body.lower():
                        check(
                            f"{label} contains '{needle}'",
                            False,
                            "missing expected content",
                        )
                        failed += 1
        print()
    else:
        print("Skipping logged-in tests (no username/password provided).")
        print("  Usage: verify_deployment.py URL USERNAME PASSWORD")
        print()

    # ── Summary ──
    if failed:
        print(f"FAILED: {failed} check(s) failed")
        return 1
    print("SUCCESS: All deployment checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
