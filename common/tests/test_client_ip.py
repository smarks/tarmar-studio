"""Client-IP resolution and the rate-limit buckets keyed on it (issue #139).

Production is gunicorn behind nginx, so ``REMOTE_ADDR`` is the proxy for every
visitor. Keying rate limits on it gave the whole site one shared bucket: ten
wrong passphrases from anybody locked everyone out for five minutes. These
tests pin every part of the fix — a forwarded header counts only when the
deployment says it is behind a proxy, the connection came from a trusted peer,
and the value is a real IP address; and when all three hold, buckets are
per-client.
"""

from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings

from common.helpers import UNKNOWN_CLIENT_IP, client_ip
from common.middleware import TrustedProxyClientIPMiddleware

# The peer nginx connects from: it proxies to gunicorn over loopback.
PROXY_ADDRESS = "127.0.0.1"
# A peer that is NOT in the trusted set — stands in for anything reaching the
# port from somewhere the proxy does not live.
UNTRUSTED_PEER = "10.0.0.1"
REAL_CLIENT = "203.0.113.7"
OTHER_CLIENT = "203.0.113.8"
SPOOFED_CLIENT = "198.51.100.66"


class ClientIPTest(TestCase):
    """``client_ip`` reads forwarded headers only when told the proxy is real."""

    def setUp(self):
        self.factory = RequestFactory()

    def _request(self, **meta):
        return self.factory.get("/", **meta)

    @override_settings(TRUST_PROXY_IP_HEADER=False)
    def test_untrusted_ignores_forwarded_headers(self):
        """Off-proxy, the headers are just strings the caller typed."""
        request = self._request(
            REMOTE_ADDR=PROXY_ADDRESS,
            HTTP_X_REAL_IP=SPOOFED_CLIENT,
            HTTP_X_FORWARDED_FOR=SPOOFED_CLIENT,
        )
        self.assertEqual(client_ip(request), PROXY_ADDRESS)

    @override_settings(TRUST_PROXY_IP_HEADER=False)
    def test_untrusted_uses_remote_addr(self):
        self.assertEqual(client_ip(self._request(REMOTE_ADDR=REAL_CLIENT)), REAL_CLIENT)

    @override_settings(TRUST_PROXY_IP_HEADER=True)
    def test_trusted_prefers_x_real_ip(self):
        """nginx writes X-Real-IP from its own $remote_addr, so it is the peer."""
        request = self._request(
            REMOTE_ADDR=PROXY_ADDRESS,
            HTTP_X_REAL_IP=REAL_CLIENT,
            HTTP_X_FORWARDED_FOR=f"{SPOOFED_CLIENT}, {REAL_CLIENT}",
        )
        self.assertEqual(client_ip(request), REAL_CLIENT)

    @override_settings(TRUST_PROXY_IP_HEADER=True)
    def test_trusted_falls_back_to_last_forwarded_hop(self):
        """$proxy_add_x_forwarded_for appends the peer, so the last hop is real.

        The left-most entry is whatever the client sent, so reading the first
        hop — the usual reflex — would take the attacker's word for it.
        """
        request = self._request(
            REMOTE_ADDR=PROXY_ADDRESS,
            HTTP_X_FORWARDED_FOR=f"{SPOOFED_CLIENT}, {REAL_CLIENT}",
        )
        self.assertEqual(client_ip(request), REAL_CLIENT)

    @override_settings(TRUST_PROXY_IP_HEADER=True)
    def test_trusted_ignores_blank_header_values(self):
        request = self._request(
            REMOTE_ADDR=PROXY_ADDRESS,
            HTTP_X_REAL_IP="   ",
            HTTP_X_FORWARDED_FOR=" , ",
        )
        self.assertEqual(client_ip(request), PROXY_ADDRESS)

    @override_settings(TRUST_PROXY_IP_HEADER=True)
    def test_trusted_falls_back_to_remote_addr_without_headers(self):
        request = self._request(REMOTE_ADDR=PROXY_ADDRESS)
        self.assertEqual(client_ip(request), PROXY_ADDRESS)

    @override_settings(TRUST_PROXY_IP_HEADER=False)
    def test_missing_remote_addr_is_named_not_empty(self):
        """A blank identifier would silently merge every such caller."""
        request = self._request()
        del request.META["REMOTE_ADDR"]
        self.assertEqual(client_ip(request), UNKNOWN_CLIENT_IP)

    @override_settings(TRUST_PROXY_IP_HEADER=True)
    def test_unparsable_peer_is_never_a_trusted_proxy(self):
        """No socket peer, no way to know the headers came through the proxy."""
        request = self._request(HTTP_X_REAL_IP=SPOOFED_CLIENT)
        del request.META["REMOTE_ADDR"]
        self.assertEqual(client_ip(request), UNKNOWN_CLIENT_IP)

    @override_settings(TRUST_PROXY_IP_HEADER=True)
    def test_untrusted_peer_cannot_forge_an_identity(self):
        """The flag describes the deployment; the peer check describes the call.

        gunicorn binds loopback on a box that also hosts melee and orge, and the
        flag is on there. Anything reaching the port from a peer that is not the
        proxy must not get to name itself.
        """
        request = self._request(
            REMOTE_ADDR=UNTRUSTED_PEER,
            HTTP_X_REAL_IP=SPOOFED_CLIENT,
            HTTP_X_FORWARDED_FOR=SPOOFED_CLIENT,
        )
        self.assertEqual(client_ip(request), UNTRUSTED_PEER)

    @override_settings(TRUST_PROXY_IP_HEADER=True, TRUSTED_PROXY_IPS={UNTRUSTED_PEER})
    def test_trusted_peer_set_is_configurable(self):
        """The same peer is believed once it is named as a proxy."""
        request = self._request(REMOTE_ADDR=UNTRUSTED_PEER, HTTP_X_REAL_IP=REAL_CLIENT)
        self.assertEqual(client_ip(request), REAL_CLIENT)

    @override_settings(TRUST_PROXY_IP_HEADER=True)
    def test_ipv6_loopback_peer_matches_its_expanded_spelling(self):
        """Addresses are compared parsed, not as text."""
        request = self._request(
            REMOTE_ADDR="0:0:0:0:0:0:0:1", HTTP_X_REAL_IP=REAL_CLIENT
        )
        self.assertEqual(client_ip(request), REAL_CLIENT)

    @override_settings(TRUST_PROXY_IP_HEADER=True, PROXY_SHARED_SECRET="s3cret")
    def test_correct_shared_secret_is_honoured(self):
        """When configured, a correct secret header still lets the peer
        check's forwarded headers through."""
        request = self._request(
            REMOTE_ADDR=PROXY_ADDRESS,
            HTTP_X_REAL_IP=REAL_CLIENT,
            HTTP_X_PROXY_SHARED_SECRET="s3cret",
        )
        self.assertEqual(client_ip(request), REAL_CLIENT)

    @override_settings(TRUST_PROXY_IP_HEADER=True, PROXY_SHARED_SECRET="s3cret")
    def test_missing_shared_secret_falls_back_to_remote_addr(self):
        """Issue #161 part 3: a trusted-peer connection that did not come
        through nginx (nginx is the only thing that can read the file the
        secret lives in) must not get to name itself."""
        request = self._request(
            REMOTE_ADDR=PROXY_ADDRESS, HTTP_X_REAL_IP=SPOOFED_CLIENT
        )
        self.assertEqual(client_ip(request), PROXY_ADDRESS)

    @override_settings(TRUST_PROXY_IP_HEADER=True, PROXY_SHARED_SECRET="s3cret")
    def test_wrong_shared_secret_falls_back_to_remote_addr(self):
        request = self._request(
            REMOTE_ADDR=PROXY_ADDRESS,
            HTTP_X_REAL_IP=SPOOFED_CLIENT,
            HTTP_X_PROXY_SHARED_SECRET="wrong-guess",
        )
        self.assertEqual(client_ip(request), PROXY_ADDRESS)

    @override_settings(TRUST_PROXY_IP_HEADER=True, PROXY_SHARED_SECRET="s3cret")
    def test_non_ascii_header_value_is_refused_not_a_crash(self):
        """WSGI decodes header bytes as latin-1, so a header value can carry
        code points outside ASCII -- exactly what the co-hosted adversary
        this check exists to stop is free to send.
        ``hmac.compare_digest`` raises ``TypeError`` on a non-ASCII ``str``,
        so an un-encoded comparison would crash this request instead of
        just failing the check."""
        request = self._request(
            REMOTE_ADDR=PROXY_ADDRESS,
            HTTP_X_REAL_IP=SPOOFED_CLIENT,
            HTTP_X_PROXY_SHARED_SECRET="wrong-\xe9-guess",
        )
        self.assertEqual(client_ip(request), PROXY_ADDRESS)

    @override_settings(TRUST_PROXY_IP_HEADER=True, PROXY_SHARED_SECRET="")
    def test_unconfigured_shared_secret_does_not_block_forwarded_headers(self):
        """Default (empty) behaves exactly like before #161 part 3 existed."""
        request = self._request(REMOTE_ADDR=PROXY_ADDRESS, HTTP_X_REAL_IP=REAL_CLIENT)
        self.assertEqual(client_ip(request), REAL_CLIENT)

    @override_settings(TRUST_PROXY_IP_HEADER=True)
    def test_unparsable_header_value_is_refused(self):
        """A header that is not an address must never become the identity.

        It would go straight into a cache key — DatabaseCache's ``cache_key`` is
        varchar(255), so an over-long one raises instead of throttling — and
        into the access log, where crafted text is log injection.
        """
        request = self._request(
            REMOTE_ADDR=PROXY_ADDRESS,
            HTTP_X_REAL_IP="not-an-ip-address",
            HTTP_X_FORWARDED_FOR="also nonsense",
        )
        self.assertEqual(client_ip(request), PROXY_ADDRESS)

    @override_settings(TRUST_PROXY_IP_HEADER=True)
    def test_overlong_header_value_is_refused(self):
        request = self._request(REMOTE_ADDR=PROXY_ADDRESS, HTTP_X_REAL_IP="9" * 4000)
        self.assertEqual(client_ip(request), PROXY_ADDRESS)

    @override_settings(TRUST_PROXY_IP_HEADER=True)
    def test_unparsable_x_real_ip_falls_through_to_forwarded_for(self):
        request = self._request(
            REMOTE_ADDR=PROXY_ADDRESS,
            HTTP_X_REAL_IP="not-an-ip-address",
            HTTP_X_FORWARDED_FOR=f"{SPOOFED_CLIENT}, {REAL_CLIENT}",
        )
        self.assertEqual(client_ip(request), REAL_CLIENT)


class TrustedProxyClientIPMiddlewareTest(TestCase):
    """The REMOTE_ADDR rewrite that reaches origami_common's ``rate_limit``."""

    def setUp(self):
        self.factory = RequestFactory()

    def _seen_remote_addr(self, **meta):
        """Run the middleware and report what a downstream view would read."""
        seen = {}
        downstream_response = HttpResponse("downstream")

        def downstream(request):
            seen["remote_addr"] = request.META.get("REMOTE_ADDR")
            seen["proxy_addr"] = request.META.get("REMOTE_ADDR_PROXY")
            return downstream_response

        middleware = TrustedProxyClientIPMiddleware(downstream)
        self.assertIs(middleware(self.factory.get("/", **meta)), downstream_response)
        return seen

    @override_settings(TRUST_PROXY_IP_HEADER=True)
    def test_rewrites_remote_addr_when_trusted(self):
        seen = self._seen_remote_addr(
            REMOTE_ADDR=PROXY_ADDRESS, HTTP_X_REAL_IP=REAL_CLIENT
        )
        self.assertEqual(seen["remote_addr"], REAL_CLIENT)
        self.assertEqual(seen["proxy_addr"], PROXY_ADDRESS)

    @override_settings(TRUST_PROXY_IP_HEADER=False)
    def test_leaves_remote_addr_alone_when_flag_is_off(self):
        seen = self._seen_remote_addr(
            REMOTE_ADDR=PROXY_ADDRESS, HTTP_X_REAL_IP=SPOOFED_CLIENT
        )
        self.assertEqual(seen["remote_addr"], PROXY_ADDRESS)
        self.assertIsNone(seen["proxy_addr"])

    @override_settings(TRUST_PROXY_IP_HEADER=True)
    def test_leaves_remote_addr_alone_for_an_untrusted_peer(self):
        """What origami_common's rate_limit reads must not be forgeable."""
        seen = self._seen_remote_addr(
            REMOTE_ADDR=UNTRUSTED_PEER, HTTP_X_REAL_IP=SPOOFED_CLIENT
        )
        self.assertEqual(seen["remote_addr"], UNTRUSTED_PEER)
        self.assertIsNone(seen["proxy_addr"])

    @override_settings(TRUST_PROXY_IP_HEADER=True)
    def test_leaves_remote_addr_alone_for_an_unparsable_header(self):
        seen = self._seen_remote_addr(
            REMOTE_ADDR=PROXY_ADDRESS, HTTP_X_REAL_IP="not-an-ip-address"
        )
        self.assertEqual(seen["remote_addr"], PROXY_ADDRESS)
        self.assertIsNone(seen["proxy_addr"])


class GateRateLimitBucketTest(TestCase):
    """Failed passphrase attempts must cost the guesser, not the whole site."""

    GATE_URL = "/gate/"
    WRONG_PASSPHRASE = "gandalf"
    ATTEMPTS_BEFORE_BLOCK = 10

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def _guess(self, **meta):
        return self.client.post(
            self.GATE_URL, {"passphrase": self.WRONG_PASSPHRASE}, **meta
        )

    def _exhaust(self, **meta):
        for attempt in range(self.ATTEMPTS_BEFORE_BLOCK):
            response = self._guess(**meta)
            self.assertEqual(response.status_code, 200, msg=f"attempt {attempt}")

    @override_settings(TRUST_PROXY_IP_HEADER=True)
    def test_one_clients_failures_do_not_block_another(self):
        """The bug: behind the proxy every visitor shared a single bucket."""
        blocked = {"REMOTE_ADDR": PROXY_ADDRESS, "HTTP_X_REAL_IP": REAL_CLIENT}
        innocent = {"REMOTE_ADDR": PROXY_ADDRESS, "HTTP_X_REAL_IP": OTHER_CLIENT}

        self._exhaust(**blocked)
        self.assertEqual(self._guess(**blocked).status_code, 429)

        self.assertEqual(self._guess(**innocent).status_code, 200)

    @override_settings(TRUST_PROXY_IP_HEADER=True)
    def test_limit_still_bites_the_client_that_earned_it(self):
        """Per-client keying must not become no limit at all."""
        guesser = {"REMOTE_ADDR": PROXY_ADDRESS, "HTTP_X_REAL_IP": REAL_CLIENT}
        self._exhaust(**guesser)

        response = self._guess(**guesser)
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "300")

    @override_settings(TRUST_PROXY_IP_HEADER=False)
    def test_forwarded_header_cannot_dodge_the_limit_off_proxy(self):
        """Without a trusted proxy, rotating the header must buy nothing."""
        self._exhaust(REMOTE_ADDR=REAL_CLIENT)

        response = self._guess(REMOTE_ADDR=REAL_CLIENT, HTTP_X_REAL_IP=OTHER_CLIENT)
        self.assertEqual(response.status_code, 429)

    @override_settings(TRUST_PROXY_IP_HEADER=True)
    def test_untrusted_peer_cannot_dodge_the_limit_by_rotating_the_header(self):
        """The flag is on in production, so this is the co-hosted-app case."""
        self._exhaust(REMOTE_ADDR=UNTRUSTED_PEER, HTTP_X_REAL_IP=REAL_CLIENT)

        response = self._guess(REMOTE_ADDR=UNTRUSTED_PEER, HTTP_X_REAL_IP=OTHER_CLIENT)
        self.assertEqual(response.status_code, 429)


class SharedCacheBackendTest(TestCase):
    """The rate-limit counters have to outlive one gunicorn worker."""

    def test_default_cache_is_the_database_backend(self):
        from django.conf import settings

        default_cache = settings.CACHES["default"]
        self.assertEqual(
            default_cache["BACKEND"], "django.core.cache.backends.db.DatabaseCache"
        )
        self.assertEqual(default_cache["LOCATION"], settings.CACHE_TABLE_NAME)

    def test_cache_table_exists_and_round_trips(self):
        """Proof the migration ran: no table, no counters, no rate limiting."""
        from django.conf import settings
        from django.db import connection

        self.assertIn(settings.CACHE_TABLE_NAME, connection.introspection.table_names())

        cache.set("issue_139_probe", 1, 60)
        self.addCleanup(cache.delete, "issue_139_probe")
        self.assertEqual(cache.get("issue_139_probe"), 1)
