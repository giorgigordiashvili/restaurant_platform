"""The QR short-link resolver on plain (non-venue) tables."""

from django.core.exceptions import ValidationError

import pytest

from apps.tables.models import TableQRCode
from apps.tables.qr_links import resolve


@pytest.fixture(autouse=True)
def _base(settings):
    settings.FRONTEND_BASE_URL = "https://example.test"


@pytest.mark.django_db
class TestResolvePlainTable:
    def test_unknown_or_malformed_returns_none(self, table_qr_code):
        assert resolve("nope-nope-nope") is None
        assert resolve("bad code!") is None
        assert resolve("") is None

    def test_inactive_code_table_or_restaurant_returns_none(self, table_qr_code, restaurant):
        table_qr_code.is_active = False
        table_qr_code.save()
        assert resolve(table_qr_code.code) is None
        table_qr_code.is_active = True
        table_qr_code.save()
        table_qr_code.table.is_active = False
        table_qr_code.table.save()
        assert resolve(table_qr_code.code) is None
        table_qr_code.table.is_active = True
        table_qr_code.table.save()
        restaurant.is_active = False
        restaurant.save()
        assert resolve(table_qr_code.code) is None

    def test_auto_goes_to_restaurant_page_with_the_code(self, table_qr_code, restaurant):
        d = resolve(table_qr_code.code)
        assert d.kind == "restaurant"
        assert d.path == f"/restaurant/{restaurant.slug}?table={table_qr_code.code}"
        assert d.url == "https://example.test" + d.path
        assert (d.restaurant_slug, d.venue_slug, d.table_code) == (restaurant.slug, None, table_qr_code.code)

    def test_restaurant_override_without_venue_has_no_via(self, table_qr_code):
        table_qr_code.destination = "restaurant"
        table_qr_code.save()
        assert "via" not in resolve(table_qr_code.code).path

    def test_venue_override_falls_back_to_restaurant_when_not_shared(self, table_qr_code):
        table_qr_code.destination = "venue"
        table_qr_code.save()
        assert resolve(table_qr_code.code).kind == "restaurant"

    def test_menu_only_has_no_table_param(self, table_qr_code, restaurant):
        table_qr_code.destination = "menu_only"
        table_qr_code.save()
        d = resolve(table_qr_code.code)
        assert (d.kind, d.path, d.table_code) == ("menu", f"/restaurant/{restaurant.slug}", None)

    def test_custom_returns_url_and_no_path(self, table_qr_code):
        table_qr_code.destination = "custom"
        table_qr_code.custom_url = "https://promo.example.com/summer?x=1"
        table_qr_code.save()
        d = resolve(table_qr_code.code)
        assert (d.kind, d.path, d.url) == ("custom", None, "https://promo.example.com/summer?x=1")

    def test_custom_without_url_falls_back_to_auto(self, table_qr_code):
        TableQRCode.objects.filter(pk=table_qr_code.pk).update(destination="custom", custom_url="")
        assert resolve(table_qr_code.code).kind == "restaurant"

    def test_record_bumps_resolves_not_scans(self, table_qr_code):
        resolve(table_qr_code.code, record=True)
        table_qr_code.refresh_from_db()
        assert (table_qr_code.resolves_count, table_qr_code.scans_count) == (1, 0)
        assert table_qr_code.last_resolved_at is not None
        resolve(table_qr_code.code)  # read-only by default
        table_qr_code.refresh_from_db()
        assert table_qr_code.resolves_count == 1


@pytest.mark.django_db
class TestModelRules:
    def test_short_link_and_legacy_direct_url(self, table_qr_code, restaurant):
        assert table_qr_code.get_qr_url() == f"https://example.test/q/{table_qr_code.code}"
        assert (
            table_qr_code.direct_url()
            == f"https://example.test/restaurant/{restaurant.slug}?table={table_qr_code.code}"
        )

    def test_new_image_records_encoded_url_and_is_current(self, table_qr_code):
        assert table_qr_code.qr_image and table_qr_code.qr_image_url == table_qr_code.get_qr_url()
        assert table_qr_code.image_is_current

    def test_legacy_image_is_flagged_and_regenerate_renames(self, table_qr_code):
        old_name = table_qr_code.qr_image.name
        TableQRCode.objects.filter(pk=table_qr_code.pk).update(qr_image_url=table_qr_code.direct_url())
        table_qr_code.refresh_from_db()
        assert not table_qr_code.image_is_current
        table_qr_code.regenerate_qr_image()
        table_qr_code.refresh_from_db()
        assert table_qr_code.image_is_current
        assert table_qr_code.qr_image.name != old_name and table_qr_code.qr_image.name.endswith(".png")

    @pytest.mark.parametrize(
        "url",
        ["", "ftp://x.example/menu", "https://user:pw@evil.example/", "https://example.test/q/other-code-here"],
    )
    def test_clean_rejects_bad_custom_urls(self, table_qr_code, url):
        table_qr_code.destination = "custom"
        table_qr_code.custom_url = url
        with pytest.raises(ValidationError) as exc:
            table_qr_code.full_clean()
        assert "custom_url" in exc.value.message_dict

    def test_clean_clears_custom_url_for_other_destinations(self, table_qr_code):
        table_qr_code.destination = "auto"
        table_qr_code.custom_url = "https://promo.example.com/"
        table_qr_code.full_clean()
        assert table_qr_code.custom_url == ""
