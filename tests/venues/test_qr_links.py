"""The QR short-link resolver at shared venues."""

import pytest

from apps.tables.models import Table
from apps.tables.qr_links import resolve
from apps.venues import services


def _qr(restaurant, number="2"):
    return Table.objects.get(restaurant=restaurant, number=number).qr_codes.first()


@pytest.mark.django_db
class TestResolveAtVenue:
    def test_auto_at_shared_table_goes_to_venue_page_with_venue_code(self, venue_pair, a):
        qr = _qr(a)
        vt = qr.table.venue_table
        d = resolve(qr.code)
        assert d.kind == "venue"
        assert d.path == f"/venue/{venue_pair.slug}?table={vt.code}"
        assert (d.restaurant_slug, d.venue_slug, d.table_code) == (a.slug, venue_pair.slug, vt.code)

    def test_auto_falls_back_when_venue_dissolved(self, venue_pair, a, b):
        qr = _qr(a)
        services.leave_venue(b)
        services.leave_venue(a)  # last member -> venue inactive
        d = resolve(qr.code)
        assert d.kind == "restaurant" and d.path.endswith(f"?table={qr.code}")

    def test_restaurant_override_adds_via_venue(self, venue_pair, a):
        qr = _qr(a)
        qr.destination = "restaurant"
        qr.save()
        d = resolve(qr.code)
        assert d.kind == "restaurant"
        assert d.path == f"/restaurant/{a.slug}?table={qr.code}&via=venue"

    def test_venue_code_resolves_to_venue_page_and_records_there(self, venue_pair, a):
        vt = venue_pair.tables.get(number="2")
        d = resolve(vt.code, record=True)
        assert (d.kind, d.path) == ("venue", f"/venue/{venue_pair.slug}?table={vt.code}")
        vt.refresh_from_db()
        assert (vt.resolves_count, vt.scans_count) == (1, 0)

    def test_venue_code_dissolved_returns_none(self, venue_pair, a, b):
        vt = venue_pair.tables.get(number="2")
        services.leave_venue(b)
        services.leave_venue(a)
        assert resolve(vt.code) is None
