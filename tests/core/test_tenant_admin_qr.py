import pytest

from apps.tables.models import TableQRCode

from .test_tenant_admin_menu import manager_client, rival  # noqa: F401  (fixtures)


@pytest.fixture(autouse=True)
def _base(settings):
    settings.FRONTEND_BASE_URL = "https://example.test"


@pytest.mark.django_db
class TestQRAdmin:
    def test_change_form_shows_destination_and_resolved_link(self, manager_client, table_qr_code):
        html = manager_client.get(f"/tenant-admin/tables/tableqrcode/{table_qr_code.pk}/change/").content.decode()
        assert 'name="destination"' in html and 'name="custom_url"' in html
        assert f"https://example.test/q/{table_qr_code.code}" in html
        assert "Short link ✓" in html

    def test_custom_url_error_rendered(self, manager_client, table_qr_code, table):
        resp = manager_client.post(
            f"/tenant-admin/tables/tableqrcode/{table_qr_code.pk}/change/",
            {
                "table": str(table.pk),
                "name": "",
                "is_active": "on",
                "destination": "custom",
                "custom_url": "https://example.test/q/abcdefghij",
            },
        )
        assert resp.status_code == 200 and "cannot point at another QR short link" in resp.content.decode()

    def test_list_shows_legacy_badge_and_action_regenerates_only_own_rows(self, manager_client, table_qr_code, rival):
        from apps.tables.models import Table

        rival_qr = TableQRCode.objects.create(table=Table.objects.create(restaurant=rival, number="1"))
        TableQRCode.objects.filter(pk__in=[table_qr_code.pk, rival_qr.pk]).update(qr_image_url="legacy")
        html = manager_client.get("/tenant-admin/tables/tableqrcode/").content.decode()
        assert "Legacy" in html and str(rival_qr.pk) not in html

        resp = manager_client.post(
            "/tenant-admin/tables/tableqrcode/",
            {"action": "regenerate_qr_images", "_selected_action": [str(table_qr_code.pk), str(rival_qr.pk)]},
        )
        assert resp.status_code == 302
        table_qr_code.refresh_from_db()
        rival_qr.refresh_from_db()
        assert table_qr_code.image_is_current and not rival_qr.image_is_current
