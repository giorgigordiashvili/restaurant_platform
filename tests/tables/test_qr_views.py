"""Public resolve endpoint, root short-link redirect, dashboard regenerate."""

from rest_framework.test import APIClient

import pytest

from apps.tables.models import TableQRCode


@pytest.fixture(autouse=True)
def _base(settings):
    settings.FRONTEND_BASE_URL = "https://example.test"


@pytest.mark.django_db
class TestQRResolveView:
    def test_known_code_shape_and_no_store(self, api_client, table_qr_code, restaurant):
        resp = api_client.get(f"/api/v1/qr/{table_qr_code.code}/")
        assert resp.status_code == 200 and "no-store" in resp["Cache-Control"]
        data = resp.json()["data"]
        assert data["kind"] == "restaurant"
        assert data["path"] == f"/restaurant/{restaurant.slug}?table={table_qr_code.code}"
        assert data["url"] == "https://example.test" + data["path"]
        table_qr_code.refresh_from_db()
        assert table_qr_code.resolves_count == 1

    def test_unknown_and_malformed_404(self, api_client, django_assert_num_queries):
        assert api_client.get("/api/v1/qr/nope-nope-nope/").status_code == 404
        with django_assert_num_queries(0):
            assert api_client.get("/api/v1/qr/bad!/").status_code == 404

    def test_not_throttled(self, api_client, table_qr_code, settings):
        settings.REST_FRAMEWORK = {
            **settings.REST_FRAMEWORK,
            "DEFAULT_THROTTLE_RATES": {**settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"], "anon": "1/hour"},
        }
        for _ in range(3):
            assert api_client.get(f"/api/v1/qr/{table_qr_code.code}/").status_code == 200


@pytest.mark.django_db
class TestQRShortLinkRedirect:
    def test_302_to_destination_with_never_cache(self, client, table_qr_code, restaurant):
        resp = client.get(f"/q/{table_qr_code.code}/")
        assert resp.status_code == 302
        assert resp["Location"] == f"https://example.test/restaurant/{restaurant.slug}?table={table_qr_code.code}"
        assert "no-store" in resp["Cache-Control"]

    def test_unknown_302_to_scan_error(self, client):
        resp = client.get("/q/nope-nope-nope/")
        assert resp.status_code == 302 and resp["Location"] == "https://example.test/scan?error=invalid"


@pytest.mark.django_db
class TestDashboardQRCodes:
    @pytest.fixture
    def owner_api(self, authenticated_owner_client, restaurant):
        authenticated_owner_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        return authenticated_owner_client

    def test_list_carries_short_link_destination_and_resolved_url(self, owner_api, table, table_qr_code):
        rows = owner_api.get(f"/api/v1/dashboard/tables/{table.id}/qr-codes/").json()["results"]
        row = next(r for r in rows if r["id"] == str(table_qr_code.id))
        assert row["qr_url"] == f"https://example.test/q/{table_qr_code.code}"
        assert row["destination"] == "auto" and row["image_is_current"] is True
        assert row["resolved_url"].startswith("https://example.test/restaurant/")

    def test_patch_custom_requires_valid_url(self, owner_api, table_qr_code):
        url = f"/api/v1/dashboard/tables/qr-codes/{table_qr_code.id}/"
        bad = owner_api.patch(url, {"destination": "custom", "custom_url": "ftp://x"}, format="json")
        assert bad.status_code == 400 and "custom_url" in bad.json()
        ok = owner_api.patch(url, {"destination": "custom", "custom_url": "https://promo.example.com/x"}, format="json")
        assert ok.status_code == 200 and ok.json()["resolved_url"] == "https://promo.example.com/x"

    def test_regenerate(self, owner_api, table_qr_code, create_restaurant, create_user):
        assert APIClient().post(f"/api/v1/dashboard/tables/qr-codes/{table_qr_code.id}/regenerate/").status_code == 401
        rival_owner = create_user(email="rival2@example.com", first_name="R", last_name="O")
        rival = create_restaurant(owner=rival_owner, name="Rival", slug="rival-2")
        rival_api = APIClient()
        rival_api.force_authenticate(rival_owner)
        rival_api.credentials(HTTP_X_RESTAURANT=rival.slug)
        assert rival_api.post(f"/api/v1/dashboard/tables/qr-codes/{table_qr_code.id}/regenerate/").status_code == 404

        TableQRCode.objects.filter(pk=table_qr_code.pk).update(qr_image_url="legacy")
        old_name = table_qr_code.qr_image.name
        resp = owner_api.post(f"/api/v1/dashboard/tables/qr-codes/{table_qr_code.id}/regenerate/")
        assert resp.status_code == 200 and resp.json()["data"]["image_is_current"] is True
        table_qr_code.refresh_from_db()
        assert table_qr_code.qr_image.name != old_name
