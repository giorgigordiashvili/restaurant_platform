import pytest

from apps.tables.models import Table, TableQRCode, TableSession
from apps.venues import services


def _cards(payload):
    return payload["data"]["restaurants"]


@pytest.mark.django_db
class TestVenueDetail:
    def test_cards_in_membership_order_without_pii(self, api_client, venue_pair, a, b):
        resp = api_client.get(f"/api/v1/venues/{venue_pair.slug}/")
        assert resp.status_code == 200
        assert resp["Cache-Control"].startswith("public")
        cards = _cards(resp.json())
        assert [c["slug"] for c in cards] == [a.slug, b.slug]
        for c in cards:
            assert {"primary_color", "is_open_now", "accepts_remote_orders", "display_order"} <= set(c)
            assert "owner" not in c and "email" not in c
        assert resp.json()["data"]["venue"]["restaurants_count"] == 2

    def test_inactive_member_excluded(self, api_client, venue_pair, b):
        b.is_active = False
        b.save(update_fields=["is_active"])
        assert len(_cards(api_client.get(f"/api/v1/venues/{venue_pair.slug}/").json())) == 1

    @pytest.mark.parametrize("state", ["unknown", "inactive"])
    def test_404(self, api_client, venue_pair, state):
        slug = "nope" if state == "unknown" else venue_pair.slug
        if state == "inactive":
            venue_pair.is_active = False
            venue_pair.save()
        assert api_client.get(f"/api/v1/venues/{slug}/").status_code == 404

    def test_content_language(self, api_client, venue_pair):
        assert api_client.get(f"/api/v1/venues/{venue_pair.slug}/", {"lang": "en"})["Content-Language"] == "en"


@pytest.mark.django_db
class TestVenueValidate:
    def test_returns_each_members_own_table_code_without_a_session(self, api_client, venue_pair, a, b):
        vt = venue_pair.tables.get(number="2")
        resp = api_client.get(f"/api/v1/venues/validate/{vt.code}/")
        assert resp.status_code == 200 and resp["Cache-Control"] == "no-store"
        data = resp.json()["data"]
        assert data["table"]["number"] == "2" and data["table"]["code"] == vt.code
        by_slug = {e["restaurant"]["slug"]: e for e in data["restaurants"]}
        for r in (a, b):
            mirror = Table.objects.get(restaurant=r, venue_table=vt)
            assert by_slug[r.slug]["table_id"] == str(mirror.pk)
            assert by_slug[r.slug]["table_code"] == mirror.qr_codes.first().code
        assert TableSession.objects.count() == 0
        vt.refresh_from_db()
        assert vt.scans_count == 1
        assert "session" not in data and "invite_code" not in resp.content.decode()

    def test_member_without_mirror_gets_nulls(self, api_client, venue_pair, b):
        vt = venue_pair.tables.get(number="1")
        Table.objects.filter(restaurant=b, venue_table=vt).delete()
        entry = next(
            e
            for e in _cards(api_client.get(f"/api/v1/venues/validate/{vt.code}/").json())
            if e["restaurant"]["slug"] == b.slug
        )
        assert entry["table_id"] is None and entry["table_code"] is None

    def test_unknown_404_and_dissolved_410(self, api_client, venue_pair):
        assert api_client.get("/api/v1/venues/validate/v_nope/").status_code == 404
        vt = venue_pair.tables.first()
        venue_pair.is_active = False
        venue_pair.save()
        resp = api_client.get(f"/api/v1/venues/validate/{vt.code}/")
        assert resp.status_code == 410 and resp.json()["error"]["code"] == "venue_dissolved"

    def test_member_code_continues_the_normal_flow(self, api_client, venue_pair, a):
        """The per-restaurant validate on a mirrored table still creates that restaurant's session."""
        mirror = Table.objects.get(restaurant=a, number="1")
        resp = api_client.get(f"/api/v1/tables/validate/{mirror.qr_codes.first().code}/")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["restaurant_slug"] == a.slug and data["session_id"]
        assert data["venue"] == {
            "slug": venue_pair.slug,
            "name": venue_pair.name,
            "table_code": mirror.venue_table.code,
            "table_number": "1",
        }
        assert TableSession.objects.filter(table=mirror).count() == 1

    def test_non_venue_table_has_venue_null_and_old_keys(self, api_client, table_qr_code):
        data = api_client.get(f"/api/v1/tables/validate/{table_qr_code.code}/").json()["data"]
        assert data["venue"] is None
        assert {"table", "restaurant", "session", "session_id", "table_number", "restaurant_slug"} <= set(data)


@pytest.mark.django_db
class TestVenueMenu:
    def test_grouped_by_restaurant_with_translations(
        self, api_client, venue_pair, a, b, create_menu_item, menu_category
    ):
        create_menu_item(restaurant=a, category=menu_category, name="Khinkali")
        resp = api_client.get(f"/api/v1/venues/{venue_pair.slug}/menu/", {"lang": "en"})
        assert resp.status_code == 200 and resp["Content-Language"] == "en"
        entries = _cards(resp.json())
        assert [e["restaurant"]["slug"] for e in entries] == [a.slug, b.slug]
        items = [i for c in entries[0]["menu"]["categories"] for i in c["items"]] + entries[0]["menu"][
            "uncategorized_items"
        ]
        assert any("translations" in i for i in items)

    def test_filter_and_non_ordering_members(self, api_client, venue_pair, a, b):
        b.accepts_remote_orders = False
        b.save(update_fields=["accepts_remote_orders"])
        entries = _cards(api_client.get(f"/api/v1/venues/{venue_pair.slug}/menu/").json())
        assert [e["restaurant"]["slug"] for e in entries] == [a.slug]
        entries = _cards(api_client.get(f"/api/v1/venues/{venue_pair.slug}/menu/", {"restaurants": "nope"}).json())
        assert entries == []


@pytest.mark.django_db
class TestRestaurantSerializersVenueField:
    def test_list_and_detail_carry_venue_ref(self, api_client, venue_pair, a, rival):
        listed = {r["slug"]: r for r in api_client.get("/api/v1/restaurants/").json()["results"]}
        assert listed[a.slug]["venue"] == {"slug": venue_pair.slug, "name": venue_pair.name}
        assert listed[rival.slug]["venue"] is None
        assert api_client.get(f"/api/v1/restaurants/{a.slug}/").json()["venue"]["slug"] == venue_pair.slug


@pytest.mark.django_db
def test_qr_urls_are_short_links_and_direct_urls_keep_legacy_format(venue_pair, a):
    mirror = Table.objects.get(restaurant=a, number="1")
    qr = mirror.qr_codes.first()
    assert qr.get_qr_url() == f"https://example.test/q/{qr.code}"
    assert qr.direct_url() == f"https://example.test/restaurant/{a.slug}?table={qr.code}"
    vt = mirror.venue_table
    assert vt.get_qr_url() == f"https://example.test/q/{vt.code}"
    assert vt.direct_url() == f"https://example.test/venue/{venue_pair.slug}?table={vt.code}"
    assert vt.code.startswith("v_") and vt.qr_image and vt.image_is_current


@pytest.mark.django_db
def test_session_restaurant_property(venue_pair, a):
    mirror = Table.objects.get(restaurant=a, number="1")
    session = TableSession.objects.create(table=mirror)
    assert session.restaurant == a
