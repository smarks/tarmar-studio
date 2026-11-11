"""The nginx config the deploy installs, and the step that installs it (#179).

Production's ``/etc/nginx/sites-available/tarmar`` had drifted from
``nginx/tarmar.conf``: it kept a ``/references/`` location that nginx served
itself, past the passphrase gate, with a directory listing and CORS for any
origin, and nothing in the deploy ever installed the repository's copy. Three
things are pinned here:

- what ``nginx/tarmar.conf`` says: no ``/references/`` location, no directory
  listing, no CORS; the three server blocks production has, with Certbot's
  lines kept exactly as it wrote them; and the scanner block included in both
  HTTPS blocks;
- that ``nginx/scanner-block.conf`` drops no path this app serves -- every
  URL pattern, the static files, the gate, the health check and the admin are
  run through its expression -- while it does drop the probes it is for;
- ``deploy.sh``'s ``install_nginx_config``: installs only what differs, keeps a
  dated backup, tests and reloads nginx, and on a failed test restores every
  file it touched, reloads, and fails the deploy. Driven against stand-ins for
  ``sudo``, ``nginx`` and ``systemctl`` in a throwaway directory.

``nginx -t`` itself is not run here: nginx is not installed on the development
machines, and the suite must not need it. The deploy runs it on the host.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from django.test import SimpleTestCase
from django.urls import URLPattern, URLResolver, get_resolver

from tarmar_studio.tests.test_deploy_script import DEPLOY_SCRIPT, REPO_ROOT

NGINX_DIR = REPO_ROOT / "nginx"
SITE_CONF = NGINX_DIR / "tarmar.conf"
SCANNER_CONF = NGINX_DIR / "scanner-block.conf"

#: What production's live file had on 2026-11-10, block by block: every one
#: must still be here, with the names it answers for.
EXPECTED_SERVER_BLOCKS = (
    (
        "listen 80;",
        "tarmar.origamisoftware.com tarmar.duckdns.org melee.origamisoftware.com",
    ),
    ("listen 443 ssl;", "tarmar.origamisoftware.com melee.origamisoftware.com"),
    ("listen 443 ssl;", "tarmar.duckdns.org"),
)
#: Certbot's own lines, which a renewal looks for and must find unchanged.
CERTBOT_LINES = (
    "    if ($host = tarmar.origamisoftware.com) {\n"
    "        return 301 https://$host$request_uri;\n"
    "    } # managed by Certbot",
    "ssl_certificate /etc/letsencrypt/live/origamisoftware.com/fullchain.pem; # managed by Certbot",
    "ssl_certificate_key /etc/letsencrypt/live/origamisoftware.com/privkey.pem; # managed by Certbot",
)
SCANNER_INCLUDE = "include /etc/nginx/snippets/tarmar-scanner-block.conf;"

#: Paths this app serves that are not URL patterns of its own.
PATHS_OUTSIDE_THE_URLCONF = (
    "/",
    "/gate/",
    "/health/",
    "/static/js/chat.js",
    "/static/css/base.css",
    "/static/images/favicon.svg",
    "/static/images/default-canon/corran-reach-map.svg",
    "/admin/",
    "/admin/login/",
    "/admin/accounts/user/1/change/",
    "/tarmar/reference/dm-rules/gm-tools/faction-generator/",
    "/tarmar/reference/public-rules/combat/action-options/movement/",
)
#: What a scanner asks for; each must be dropped, or the block is not loaded.
PROBES = (
    "/.env",
    "/.env.bak",
    "/.git/config",
    "/wp-login.php",
    "/wp-admin/",
    "/phpmyadmin/",
    "/index.php",
    "/cgi-bin/test.cgi",
    "/vendor/phpunit/x",
    "/backup.sql",
    "/a/../b",
    "/actuator/health",
)
#: A value for each converter a route may carry, so every pattern becomes one
#: concrete path.
SAMPLE_VALUES = {
    "int": "7",
    "str": "sample",
    "slug": "sample-slug",
    "uuid": "123e4567-e89b-12d3-a456-426614174000",
    "path": "dm-rules/gm-tools/faction-generator",
}
CONVERTER = re.compile(r"<(?:(?P<converter>\w+):)?(?P<name>\w+)>")


def _server_blocks(text: str) -> list[str]:
    """Each top-level ``server { ... }`` of ``text``, braces matched."""
    blocks: list[str] = []
    depth = 0
    start: int | None = None
    for index, character in enumerate(text):
        if character == "{":
            if depth == 0:
                start = text.rfind("server", 0, index)
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0 and start is not None:
                blocks.append(text[start : index + 1])
                start = None
    return blocks


def scanner_expression(conf_text: str) -> re.Pattern[str]:
    """The ``location ~* "..."`` expression of the scanner block, as Python reads it."""
    match = re.search(r'location ~\* "(?P<expression>.*)" \{', conf_text)
    assert match is not None, "no location ~* block in the scanner config"
    # nginx's ~* is case-insensitive; the expression uses PCRE syntax Python
    # shares. One spelling differs on the way in: the file's [\\/] reaches
    # Python as the class [\\/] (backslash or slash) where nginx, after its
    # own unescaping of the quoted string, sees [\/]; both match "/".
    return re.compile(match.group("expression"), re.IGNORECASE)


def sample_paths(patterns, prefix: str = "") -> list[str]:
    """One concrete path per URL pattern the resolver holds."""
    paths: list[str] = []
    for pattern in patterns:
        route = str(pattern.pattern)
        if isinstance(pattern, URLResolver):
            paths.extend(sample_paths(pattern.url_patterns, prefix + route))
        elif isinstance(pattern, URLPattern):
            filled = CONVERTER.sub(
                lambda found: SAMPLE_VALUES[found.group("converter") or "str"],
                prefix + route,
            )
            paths.append("/" + filled)
    return paths


class SiteConfTest(SimpleTestCase):
    """What ``nginx/tarmar.conf`` says."""

    def setUp(self) -> None:
        super().setUp()
        self.text = SITE_CONF.read_text()
        self.blocks = _server_blocks(self.text)

    def test_no_references_location_no_listing_no_cors(self):
        self.assertNotIn("/references/", self.text)
        self.assertNotIn("autoindex", self.text)
        self.assertNotIn("Access-Control-Allow", self.text)

    def test_every_server_block_production_had_is_present(self):
        self.assertEqual(len(self.blocks), len(EXPECTED_SERVER_BLOCKS))
        for block, (listen, names) in zip(
            self.blocks, EXPECTED_SERVER_BLOCKS, strict=True
        ):
            with self.subTest(names=names):
                self.assertIn(listen, block)
                self.assertIn(f"server_name {names};", block)

    def test_certbot_s_lines_are_kept_exactly(self):
        for line in CERTBOT_LINES:
            with self.subTest(line=line.splitlines()[0]):
                self.assertIn(line, self.text)
        self.assertEqual(self.text.count("# managed by Certbot"), 3)

    def test_both_https_blocks_include_the_scanner_block_and_proxy_the_app(self):
        for block in self.blocks[1:]:
            with self.subTest(block=block.splitlines()[0]):
                self.assertIn(SCANNER_INCLUDE, block)
                self.assertIn("proxy_pass http://tarmar_backend;", block)
                self.assertIn("location /static/ {", block)
                self.assertIn("include /etc/letsencrypt/options-ssl-nginx.conf;", block)

    def test_the_redirect_block_only_redirects(self):
        self.assertNotIn("location", self.blocks[0])
        self.assertIn("return 301 https://$host$request_uri;", self.blocks[0])

    def test_the_header_says_the_deploy_installs_it(self):
        self.assertIn("deploy.sh installs it", self.text)
        self.assertIn("install_nginx_config", self.text)


class ScannerBlockTest(SimpleTestCase):
    """The block drops probes and nothing this app serves."""

    def setUp(self) -> None:
        super().setUp()
        self.text = SCANNER_CONF.read_text()
        self.expression = scanner_expression(self.text)

    def test_it_closes_the_connection_without_a_reply(self):
        self.assertIn("return 444;", self.text)

    def test_no_path_the_app_serves_is_dropped(self):
        paths = sample_paths(get_resolver().url_patterns) + list(
            PATHS_OUTSIDE_THE_URLCONF
        )
        self.assertGreater(len(paths), 100, "the sweep found too few URL patterns")
        dropped = [path for path in paths if self.expression.search(path)]
        self.assertEqual(dropped, [])

    def test_the_probes_it_is_for_are_dropped(self):
        for probe in PROBES:
            with self.subTest(probe=probe):
                self.assertIsNotNone(self.expression.search(probe))
        self.assertIsNotNone(
            self.expression.search("/WP-LOGIN.PHP"), "case-insensitive"
        )


# --- install_nginx_config -----------------------------------------------------

PASS_THROUGH_SUDO = """#!/bin/bash
exec "$@"
"""

#: Records each call. Its first ``nginx -t`` exits as FAKE_NGINX_EXIT says (0
#: by default): the new config being refused. Later calls pass, as the restored
#: config does, unless FAKE_NGINX_ALWAYS_FAILS is set: then every call fails, as
#: when another site's config on the host is broken.
RECORDING_NGINX = """#!/bin/bash
echo "nginx $*" >> "$FAKE_CALLS"
if [ -n "${FAKE_NGINX_ALWAYS_FAILS:-}" ]; then
    exit 1
fi
if [ "${FAKE_NGINX_EXIT:-0}" -ne 0 ] && [ "$(grep -c '^nginx' "$FAKE_CALLS")" -eq 1 ]; then
    exit "$FAKE_NGINX_EXIT"
fi
exit 0
"""

RECORDING_SYSTEMCTL = """#!/bin/bash
echo "systemctl $*" >> "$FAKE_CALLS"
exit 0
"""


def stand_ins(directory: Path) -> Path:
    """``sudo``, ``nginx`` and ``systemctl`` stand-ins on their own PATH entry."""
    fake_bin = directory / "bin"
    fake_bin.mkdir()
    for name, body in (
        ("sudo", PASS_THROUGH_SUDO),
        ("nginx", RECORDING_NGINX),
        ("systemctl", RECORDING_SYSTEMCTL),
    ):
        script = fake_bin / name
        script.write_text(body)
        script.chmod(0o755)
    return fake_bin


class InstallStepHost:
    """A throwaway host: a release checkout with nginx/, and the install targets."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.app_dir = root / "app"
        self.app_dir.mkdir()
        self.release = root / "release"
        shutil.copytree(NGINX_DIR, self.release / "nginx")
        self.site_target = root / "etc" / "sites-available" / "tarmar"
        self.scanner_target = root / "etc" / "snippets" / "tarmar-scanner-block.conf"
        self.calls = root / "calls.log"
        self.fake_bin = stand_ins(root)

    def install(
        self, nginx_exit: int = 0, *, nginx_always_fails: bool = False
    ) -> subprocess.CompletedProcess[str]:
        """Source deploy.sh and run install_nginx_config against this host."""
        self.calls.write_text("")
        return subprocess.run(
            [
                "bash",
                "-c",
                'set -e; source "$1"; PATH="$2:$PATH"; install_nginx_config "$3"',
                "install",
                str(DEPLOY_SCRIPT),
                str(self.fake_bin),
                str(self.release),
            ],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "TARMAR_APP_DIR": str(self.app_dir),
                "TARMAR_NGINX_SITE_CONF": str(self.site_target),
                "TARMAR_NGINX_SCANNER_CONF": str(self.scanner_target),
                "FAKE_CALLS": str(self.calls),
                "FAKE_NGINX_EXIT": str(nginx_exit),
                "FAKE_NGINX_ALWAYS_FAILS": "1" if nginx_always_fails else "",
            },
            check=False,
        )

    def recorded(self) -> list[str]:
        return self.calls.read_text().splitlines()

    def backups_of(self, target: Path) -> list[Path]:
        return sorted(target.parent.glob(f"{target.name}.bak-*"))


class InstallNginxConfigTest(SimpleTestCase):
    """``deploy.sh``'s install step, end to end against stand-ins."""

    def setUp(self) -> None:
        super().setUp()
        import tempfile

        self.scratch = Path(tempfile.mkdtemp(prefix="tarmar-nginx-"))
        self.addCleanup(shutil.rmtree, self.scratch)
        self.host = InstallStepHost(self.scratch)

    def test_a_first_install_writes_both_files_tests_and_reloads(self):
        result = self.host.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.host.site_target.read_text(), SITE_CONF.read_text())
        self.assertEqual(self.host.scanner_target.read_text(), SCANNER_CONF.read_text())
        self.assertEqual(self.host.recorded(), ["nginx -t", "systemctl reload nginx"])
        self.assertEqual(self.host.backups_of(self.host.site_target), [])
        self.assertIn("Installed", result.stdout)

    def test_a_second_run_changes_and_reloads_nothing(self):
        self.host.install()
        result = self.host.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.host.recorded(), [])
        self.assertIn("already the repository's", result.stdout)
        self.assertEqual(self.host.backups_of(self.host.site_target), [])

    def test_a_drifted_file_is_replaced_with_a_dated_backup_kept(self):
        self.host.install()
        self.host.site_target.write_text("# drifted by hand\n")
        result = self.host.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.host.site_target.read_text(), SITE_CONF.read_text())
        backups = self.host.backups_of(self.host.site_target)
        self.assertEqual(len(backups), 1)
        self.assertRegex(backups[0].name, r"^tarmar\.bak-\d{8}-\d{6}$")
        self.assertEqual(backups[0].read_text(), "# drifted by hand\n")
        # The scanner block matched already, so only the site file was replaced.
        self.assertEqual(self.host.backups_of(self.host.scanner_target), [])
        self.assertEqual(self.host.recorded(), ["nginx -t", "systemctl reload nginx"])

    def test_a_config_nginx_refuses_is_rolled_back_and_fails_the_deploy(self):
        self.host.install()
        self.host.site_target.write_text("# the config that was live\n")
        self.host.scanner_target.unlink()
        result = self.host.install(nginx_exit=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("previous config was restored", result.stdout + result.stderr)
        # The site file is back as it was; the scanner file, new this run, is gone.
        self.assertEqual(
            self.host.site_target.read_text(), "# the config that was live\n"
        )
        self.assertFalse(self.host.scanner_target.exists())
        # Tested, refused, restored, tested again and reloaded on the old config.
        self.assertEqual(
            self.host.recorded(), ["nginx -t", "nginx -t", "systemctl reload nginx"]
        )

    def test_a_host_whose_restored_config_still_fails_says_so_and_reloads_nothing(self):
        """The restored files were live a moment ago, so a second refusal is
        another site's config on the host: named as such, and no reload."""
        self.host.install()
        self.host.site_target.write_text("# the config that was live\n")
        result = self.host.install(nginx_always_fails=True)
        self.assertNotEqual(result.returncode, 0)
        said = result.stdout + result.stderr
        self.assertIn("nginx still refuses the host's config", said)
        self.assertIn("another site's config is broken", said)
        self.assertIn("nothing was reloaded", said)
        self.assertNotIn(
            "the previous config was restored and the deploy stopped", said
        )
        self.assertEqual(
            self.host.site_target.read_text(), "# the config that was live\n"
        )
        self.assertEqual(self.host.recorded(), ["nginx -t", "nginx -t"])

    def test_a_release_without_the_files_fails_before_touching_the_host(self):
        shutil.rmtree(self.host.release / "nginx")
        result = self.host.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing from the release", result.stdout + result.stderr)
        self.assertFalse(self.host.site_target.exists())
        self.assertEqual(self.host.recorded(), [])
