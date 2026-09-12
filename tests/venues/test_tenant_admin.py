import re

import pytest

from apps.tables.models import Table
from apps.venues.models import VenueMember, VenueShareRequest

PAGE = "/tenant-admin/venues/venuesharerequest/"


@pytest.mark.django_db
class TestSharedVenuePage:
    def test_renders_when_not_in_venue(self, a_admin):
        html = a_admin.get(PAGE).content.decode()
        assert 'data-testid="shared-venue-page"' in html and "Not part of a shared venue" in html
        assert 'data-testid="invite-form"' in html

    def test_renders_venue_tables_and_members(self, venue_pair, a_admin, a, b):
        html = a_admin.get(PAGE).content.decode()
        assert venue_pair.name in html and b.name in html
        assert len(re.findall(r'href="[^"]*venue_qr_codes/[^"]*"', html)) == 3
        assert "Leave venue" in html and 'data-testid="add-table-form"' in html

    def test_invite_accept_decline_cancel_leave(self, a_admin, b_admin, a, b, a_layout, b_layout, mailoutbox):
        resp = a_admin.post(PAGE + "invite/", {"to_restaurant": b.slug, "venue_name": "Food Hall", "message": "hi"})
        assert resp.status_code == 302
        req = VenueShareRequest.objects.get()
        assert mailoutbox[-1].to == [b.owner.email]

        html = b_admin.get(PAGE).content.decode()
        assert a.name in html and 'name="layout"' in html

        assert (
            b_admin.post(PAGE + f"{req.pk}/accept/", {"layout": "ours", "venue_name": "Food Hall"}).status_code == 302
        )
        assert sorted(
            Table.objects.filter(restaurant=a, venue_table__isnull=False).values_list("number", flat=True)
        ) == ["2", "9"]

        assert (
            a_admin.post(
                PAGE + "tables/add/", {"number": "20", "capacity": "4", "min_capacity": "1", "shape": "round"}
            ).status_code
            == 302
        )
        assert Table.objects.filter(number="20", venue_table__isnull=False).count() == 2

        assert b_admin.post(PAGE + "leave/").status_code == 302
        assert not VenueMember.objects.filter(restaurant=b).exists()

    def test_rival_cannot_act_on_someone_elses_request(self, pending_request, rival_admin_client, b):
        resp = rival_admin_client.post(PAGE + f"{pending_request.pk}/accept/", {"layout": "ours"})
        assert resp.status_code == 302  # bounced back with an error message
        pending_request.refresh_from_db()
        assert pending_request.status == "pending"
        assert b.name not in rival_admin_client.get(PAGE).content.decode()

    def test_waiter_cannot_see_page_or_post(self, a, a_waiter, b, a_layout):
        from .conftest import _admin

        client = _admin(a_waiter, a)
        assert client.get(PAGE).status_code == 403
        assert client.post(PAGE + "invite/", {"to_restaurant": b.slug}).status_code == 403

    def test_stock_add_and_change_are_disabled(self, pending_request, b_admin, rival):
        assert b_admin.get(PAGE + "add/").status_code == 403
        assert b_admin.get(PAGE + f"{pending_request.pk}/change/").status_code == 403
        assert rival.name not in b_admin.get(PAGE).content.decode()


@pytest.fixture
def rival_admin_client(rival_manager, rival):
    from .conftest import _admin

    return _admin(rival_manager, rival)


@pytest.mark.django_db
class TestSharedTableRows:
    def test_shared_table_form_locks_layout_fields_and_shows_badge(self, venue_pair, b_admin, b):
        mirror = Table.objects.get(restaurant=b, number="1")
        html = b_admin.get(f"/tenant-admin/tables/table/{mirror.pk}/change/").content.decode()
        assert 'name="number"' not in html and 'name="capacity"' not in html
        assert 'name="status"' in html and 'name="position_x"' in html
        assert 'name="venue_table"' not in html
        assert "Shared (venue)" in b_admin.get("/tenant-admin/tables/table/").content.decode()

    def test_private_table_still_editable_and_shared_undeletable(self, venue_pair, b_admin, b):
        private = Table.objects.get(restaurant=b, number="9")
        assert 'name="number"' in b_admin.get(f"/tenant-admin/tables/table/{private.pk}/change/").content.decode()
        shared = Table.objects.get(restaurant=b, number="2")
        assert b_admin.get(f"/tenant-admin/tables/table/{shared.pk}/delete/").status_code == 403
