"""
Tests for the Caddy on-demand TLS `ask` endpoint.

This endpoint is the only guard against unbounded certificate issuance: any
domain someone points at our IP reaches Caddy, which asks us whether to go get
a Let's Encrypt cert for it. A permissive answer burns the account's rate
limit, so the "deny" cases matter as much as the "allow" ones.
"""

from django.core.cache import cache

import pytest

TLS_CHECK_URL = "/api/v1/tls-check/"
ADMIN_DOMAIN = "admin.aimenu.ge"


@pytest.fixture(autouse=True)
def _settings_and_cache(settings):
    settings.ADMIN_DOMAIN = ADMIN_DOMAIN
    # The test settings use DummyCache, which would make the caching
    # behaviour untestable (and silently hide a regression there).
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db
class TestTlsCheckAllows:
    def test_active_restaurant_subdomain_is_allowed(self, client, restaurant):
        resp = client.get(TLS_CHECK_URL, {"domain": f"{restaurant.slug}.{ADMIN_DOMAIN}"})
        assert resp.status_code == 200

    def test_admin_domain_itself_is_allowed(self, client):
        resp = client.get(TLS_CHECK_URL, {"domain": ADMIN_DOMAIN})
        assert resp.status_code == 200

    def test_lookup_is_case_and_trailing_dot_insensitive(self, client, restaurant):
        # Caddy passes the SNI through verbatim; browsers and resolvers may
        # hand us an uppercase or fully-qualified (trailing dot) form.
        resp = client.get(TLS_CHECK_URL, {"domain": f"{restaurant.slug.upper()}.{ADMIN_DOMAIN}."})
        assert resp.status_code == 200


@pytest.mark.django_db
class TestTlsCheckDenies:
    def test_unknown_slug_is_denied(self, client):
        resp = client.get(TLS_CHECK_URL, {"domain": f"no-such-restaurant.{ADMIN_DOMAIN}"})
        assert resp.status_code == 403

    def test_inactive_restaurant_is_denied(self, client, restaurant):
        restaurant.is_active = False
        restaurant.save(update_fields=["is_active"])
        resp = client.get(TLS_CHECK_URL, {"domain": f"{restaurant.slug}.{ADMIN_DOMAIN}"})
        assert resp.status_code == 403

    def test_foreign_domain_is_denied(self, client):
        resp = client.get(TLS_CHECK_URL, {"domain": "evil.example.com"})
        assert resp.status_code == 403

    def test_domain_merely_ending_in_our_name_is_denied(self, client):
        # "notadmin.aimenu.ge.evil.com" and friends must not slip through a
        # naive suffix check.
        for domain in (
            f"evil.com.{ADMIN_DOMAIN}.evil.com",
            f"x{ADMIN_DOMAIN}",
            f"{ADMIN_DOMAIN}.evil.com",
        ):
            resp = client.get(TLS_CHECK_URL, {"domain": domain})
            assert resp.status_code == 403, domain

    def test_nested_subdomain_is_denied(self, client, restaurant):
        resp = client.get(TLS_CHECK_URL, {"domain": f"a.{restaurant.slug}.{ADMIN_DOMAIN}"})
        assert resp.status_code == 403

    def test_reserved_subdomain_is_denied(self, client):
        resp = client.get(TLS_CHECK_URL, {"domain": f"www.{ADMIN_DOMAIN}"})
        assert resp.status_code == 403

    def test_missing_domain_is_denied(self, client):
        assert client.get(TLS_CHECK_URL).status_code == 403

    def test_overlong_domain_is_denied(self, client):
        resp = client.get(TLS_CHECK_URL, {"domain": "a" * 250 + f".{ADMIN_DOMAIN}"})
        assert resp.status_code == 403


@pytest.mark.django_db
class TestTlsCheckOperational:
    def test_post_is_rejected(self, client, restaurant):
        # Caddy only ever GETs; anything else is not us.
        resp = client.post(TLS_CHECK_URL, {"domain": f"{restaurant.slug}.{ADMIN_DOMAIN}"})
        assert resp.status_code == 405

    def test_verdict_is_cached(self, client, restaurant, django_assert_num_queries):
        domain = f"{restaurant.slug}.{ADMIN_DOMAIN}"
        assert client.get(TLS_CHECK_URL, {"domain": domain}).status_code == 200
        # Second handshake for the same name must not hit the database.
        with django_assert_num_queries(0):
            assert client.get(TLS_CHECK_URL, {"domain": domain}).status_code == 200

    def test_database_error_denies_rather_than_falls_open(self, client, monkeypatch):
        import apps.core.views as core_views

        def boom(domain):
            raise RuntimeError("db is down")

        monkeypatch.setattr(core_views, "_tls_domain_allowed", boom)
        resp = client.get(TLS_CHECK_URL, {"domain": f"anything.{ADMIN_DOMAIN}"})
        assert resp.status_code == 403


@pytest.mark.django_db
def test_registered_custom_domain_is_allowed(client, restaurant, settings):
    from apps.ordering.models import RestaurantDomain

    settings.ADMIN_DOMAIN = "admin.aimenu.ge"
    RestaurantDomain.objects.create(restaurant=restaurant, domain="order.example.ge")
    assert client.get("/api/v1/tls-check/", {"domain": "ORDER.example.ge."}).status_code == 200
    assert client.get("/api/v1/tls-check/", {"domain": "other.example.ge"}).status_code == 403
