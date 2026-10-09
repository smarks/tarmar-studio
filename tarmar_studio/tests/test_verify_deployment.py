"""``scripts/verify_deployment.py`` against a stand-in site (issue #652).

This is the check a deploy now runs *before* it moves traffic, so what it can
and cannot fail on decides whether a broken release reaches users. The tests
drive the real script over real HTTP against a small server that answers the
way the deployed site does — including the two things that make a colour's own
gunicorn port different from the site behind nginx:

* production's ``ALLOWED_HOSTS`` has no ``127.0.0.1``, so a request that does
  not carry nginx's ``Host`` header is answered 400 before any page is built;
* ``SECURE_SSL_REDIRECT`` is on, and gunicorn only marks a request secure when
  a trusted proxy sent ``X-Forwarded-Proto: https``, so a request without it
  is redirected before URL resolution and nothing below the middleware runs;
* ``SESSION_COOKIE_SECURE`` is on, so the cookie the gate sets is marked
  ``Secure`` and a cookie jar withholds it from a plain-http request unless
  the checker's policy knows the connection stands in for the https one.

The stand-in redirects to a local path rather than to an absolute ``https://``
URL, which is what SecurityMiddleware really sends: these tests must not reach
the network. What matters is that the script never provokes the redirect.
"""

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from common.public_paths import LOGIN_PATH
from scripts.verify_deployment import main as verify_main

PUBLIC_HOST = "tarmar.example"
CSRF_TOKEN = "stub-csrf-token"
# What ManifestStaticFilesStorage writes into a page: the file's content hash
# is part of its name, so it changes whenever the source file does.
ICON_PATH = "/static/images/favicon.e2c99b851269.svg"
ICON_LINK_TAG = f'<link rel="icon" type="image/svg+xml" href="{ICON_PATH}">'


# Every path the script asks for, and the body the stand-in answers with. The
# content each check greps for is in the body; check_html_basics also wants
# more than 50 characters and a closed <title>.
PAGE_BODIES = {
    "/": "Tarmar Studio home, the front door of the deployment under test.",
    "/tarmar/": (
        f"{ICON_LINK_TAG} Tarmar Studio home, with enough body text for a page."
    ),
    "/tarmar/about/": "About Tarmar Studio, a page with a paragraph on it.",
    LOGIN_PATH: 'Sign in: <input name="username"><input name="password">',
    "/tarmar/accounts/register/": "Register for an account on this deployment.",
    "/tarmar/reference/": "Rules reference, the table of contents of the rules.",
    "/tarmar/characters/": "Characters belonging to the signed-in account.",
    "/tarmar/characters/create/": "Create a character by rolling its attributes.",
    "/tarmar/gm/": "Game Master chat: type a message and press Send to play.",
    "/tarmar/accounts/profile/": "Your profile, the account settings page.",
    "/tarmar/accounts/users/": "Users of this deployment, listed for an admin.",
}

# Pages the script expects to be answered with a redirect to login while it is
# signed out, and to render once it has signed in.
LOGIN_REQUIRED = {"/tarmar/characters/", "/tarmar/gm/", "/tarmar/accounts/profile/"}

GATE_COOKIE = "gatesession=granted"

GATE_PAGE = (
    "<title>Speak, friend</title>"
    '<form method="post" action="/gate/">'
    f'<input type="hidden" name="csrfmiddlewaretoken" value="{CSRF_TOKEN}">'
    '<input name="passphrase" type="password"></form>'
)


@dataclass
class StandInSite:
    """What the stand-in serves, and what it saw while serving it."""

    require_host: str = PUBLIC_HOST
    require_forwarded_proto: bool = True
    broken_page: str | None = None
    # Static paths this release did not collect, so nginx has nothing to serve.
    missing_static: set[str] = field(default_factory=set)
    # A gate that keeps answering with its own form: a wrong passphrase, a
    # failed CSRF check, or a session cookie that never came back.
    refuse_the_passphrase: bool = False
    seen_hosts: list[str] = field(default_factory=list)
    served_static: list[str] = field(default_factory=list)
    signed_in: bool = False


def build_handler(site: StandInSite) -> type[BaseHTTPRequestHandler]:
    """A request handler serving ``site``.

    Args:
        site: The stand-in's configuration and record of what it saw.

    Returns:
        A handler class for ``HTTPServer``.
    """

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:
            """Keep the stand-in out of the test output."""

        def _send(self, status: int, body: str, headers: dict[str, str] | None = None):
            encoded = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(encoded)

        def _gate_is_needed(self) -> bool:
            """The gate stands until the request brings back its cookie.

            Secure and HttpOnly, as the deployed site sets it. A checker that
            cannot return it sees the gate page for every page it asks for,
            which is how a healthy release looked broken.
            """
            if self.path == "/health/":
                return False
            return GATE_COOKIE not in self.headers.get("Cookie", "")

        def _mispresented(self) -> str | None:
            """Why this request would never have come through nginx, if so."""
            host = self.headers.get("Host", "")
            site.seen_hosts.append(host)
            if host.split(":")[0] != site.require_host:
                return "Bad Request (400)"
            return None

        def do_GET(self) -> None:  # noqa: N802  (BaseHTTPRequestHandler's name)
            if self.path == "/health/":
                self._send(200, json.dumps({"status": "ok"}))
                return
            refusal = self._mispresented()
            if refusal:
                self._send(400, f"<title>400</title>{refusal}")
                return
            if (
                site.require_forwarded_proto
                and self.headers.get("X-Forwarded-Proto") != "https"
            ):
                # What SecurityMiddleware does, kept local: see the module
                # docstring.
                self._send(301, "", {"Location": "/ssl-required"})
                return
            if self.path.startswith("/static/"):
                if self.path in site.missing_static:
                    self._send(404, "<title>404</title>No such static file.")
                    return
                site.served_static.append(self.path)
                self._send(200, "<svg></svg>")
                return
            if self._gate_is_needed():
                self._send(200, f"<title>Gate</title>{GATE_PAGE}")
                return
            if self.path in LOGIN_REQUIRED and not site.signed_in:
                self._send(302, "", {"Location": LOGIN_PATH})
                return
            if self.path == site.broken_page:
                self._send(200, "<title>Error</title>Server Error (500)")
                return
            body = PAGE_BODIES.get(self.path)
            if body is None:
                self._send(404, "<title>404</title>No page at this address.")
                return
            self._send(200, f"<title>Tarmar</title>{body}")

        def do_POST(self) -> None:  # noqa: N802
            refusal = self._mispresented()
            if refusal:
                self._send(400, f"<title>400</title>{refusal}")
                return
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            referer = self.headers.get("Referer", "")
            if not referer.startswith(f"https://{site.require_host}"):
                # Django's CSRF referer check on a request it believes secure.
                self._send(403, "<title>403</title>CSRF verification failed.")
                return
            if self.path == "/gate/":
                if site.refuse_the_passphrase:
                    self._send(200, f"<title>Gate</title>{GATE_PAGE}")
                    return
                self._send(
                    200,
                    f"<title>Tarmar</title>{PAGE_BODIES['/tarmar/']}",
                    {"Set-Cookie": f"{GATE_COOKIE}; Path=/; Secure; HttpOnly"},
                )
                return
            if self.path == LOGIN_PATH:
                site.signed_in = True
                self._send(200, f"<title>Tarmar</title>{PAGE_BODIES['/tarmar/']}")
                return
            self._send(404, "<title>404</title>No page at this address.")

    return Handler


@pytest.fixture
def stand_in_site():
    """Serve a stand-in deployment on a loopback port for one test."""
    site = StandInSite()
    server = HTTPServer(("127.0.0.1", 0), build_handler(site))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    site.base_url = f"http://127.0.0.1:{server.server_port}"  # type: ignore[attr-defined]
    try:
        yield site
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_a_colour_is_verified_through_its_own_port(stand_in_site) -> None:
    """The pre-switch check the deploy runs: pages rendered, nothing skipped."""
    exit_status = verify_main(
        [
            stand_in_site.base_url,
            "verifier",
            "password",
            "--as-proxied-host",
            PUBLIC_HOST,
        ]
    )

    assert exit_status == 0
    # It presented itself as nginx does, on every request but none of them
    # addressed to 127.0.0.1.
    assert set(stand_in_site.seen_hosts) == {PUBLIC_HOST}


def test_without_the_proxy_headers_the_site_never_renders(stand_in_site) -> None:
    """Why the check needs --as-proxied-host: production refuses the rest."""
    exit_status = verify_main([stand_in_site.base_url])

    assert exit_status == 1
    assert any("127.0.0.1" in host for host in stand_in_site.seen_hosts)


def test_a_release_that_500s_on_a_page_fails_the_check(stand_in_site) -> None:
    """The failure health_check.py cannot see, which is the point of #652.

    ``/health/`` is a JsonResponse doing SELECT 1, so it answers happily while
    every rendered page is an error. This check reads the pages.
    """
    stand_in_site.broken_page = "/tarmar/about/"

    exit_status = verify_main(
        [stand_in_site.base_url, "--as-proxied-host", PUBLIC_HOST]
    )

    assert exit_status == 1


def test_the_script_is_what_the_deploy_runs_before_switching() -> None:
    """deploy.sh asks for this script by name, with the proxied host."""
    deploy_script = (
        Path(__file__).resolve().parent.parent.parent / "deploy.sh"
    ).read_text(encoding="utf-8")
    verify_release = deploy_script[deploy_script.index("verify_release() {") :]
    body = verify_release[: verify_release.index("\n}")]
    assert "scripts/verify_deployment.py" in body
    assert "--as-proxied-host" in body


def test_the_icon_check_follows_what_the_release_rendered(stand_in_site) -> None:
    """Issue #669: the hashed name comes from the page, not from the script."""
    exit_status = verify_main(
        [stand_in_site.base_url, "--as-proxied-host", PUBLIC_HOST]
    )

    assert exit_status == 0
    assert stand_in_site.served_static == [ICON_PATH]


def test_an_icon_the_release_cannot_serve_fails_the_check(stand_in_site) -> None:
    """A page linking a static file nginx has not got is a failed deploy."""
    stand_in_site.missing_static = {ICON_PATH}

    exit_status = verify_main(
        [stand_in_site.base_url, "--as-proxied-host", PUBLIC_HOST]
    )

    assert exit_status == 1


def test_no_static_file_name_is_pinned_in_the_script() -> None:
    """A hash in the source fails a deploy on a cosmetic change to the file."""
    source = (
        Path(__file__).resolve().parent.parent.parent
        / "scripts"
        / "verify_deployment.py"
    ).read_text(encoding="utf-8")
    checks = source[source.index("def main(") :]
    assert ".svg" not in checks


def test_a_gate_that_never_opens_is_reported(stand_in_site) -> None:
    """The gate POST answers 200 with its own form again on every refusal.

    Taking that for success meant checking every later page against the gate
    page: a wrong passphrase read as a site with no working page on it, and a
    withheld session cookie read the same way.
    """
    stand_in_site.refuse_the_passphrase = True

    exit_status = verify_main(
        [stand_in_site.base_url, "--as-proxied-host", PUBLIC_HOST]
    )

    assert exit_status == 1
