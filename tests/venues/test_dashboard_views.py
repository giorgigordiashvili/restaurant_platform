import pytest

from apps.tables.models import Table
from apps.venues import services
from apps.venues.models import VenueMember, VenueShareRequest

URL = "/api/v1/dashboard/venue/"


def _pii_free(obj):
    if isinstance(obj, dict):
        assert not ({"email", "owner", "phone", "bog_payout_iban", "invite_code"} & set(obj)), obj.keys()
        for v in obj.values():
            _pii_free(v)
    elif isinstance(obj, list):
        for v in obj:
            _pii_free(v)


@pytest.mark.django_db
class TestState:
    def test_not_in_venue(self, a_api):
        data = a_api.get(URL).json()["data"]
        assert data["venue"] is None and data["members"] == [] and data["permissions"]["can_manage"] is True
        assert data["permissions"]["can_leave"] is False

    def test_in_venue(self, venue_pair, a_api, a, b):
        data = a_api.get(URL).json()["data"]
        assert data["venue"]["slug"] == venue_pair.slug and data["shared_tables_count"] == 3
        assert [m["slug"] for m in data["members"]] == [a.slug, b.slug]
        assert data["members"][0]["is_me"] is True and data["members"][0]["is_layout_seed"] is True
        _pii_free(data)

    def test_requires_auth_and_header(self, api_client, a_manager):
        assert api_client.get(URL).status_code == 401
        api_client.force_authenticate(a_manager)
        assert api_client.get(URL).status_code == 403  # no X-Restaurant -> IsTenantManager denies


@pytest.mark.django_db
class TestRequests:
    def test_send_accept_flow_as_managers(self, a_api, b_api, a, b, a_layout, b_layout, mailoutbox):
        resp = a_api.post(URL + "requests/", {"to_restaurant": b.slug, "venue_name": "Food Hall"}, format="json")
        assert resp.status_code == 201, resp.json()
        req_id = resp.json()["data"]["id"]
        assert resp.json()["data"]["direction"] == "outgoing"
        assert mailoutbox[-1].to == [b.owner.email]

        # only the two parties see it
        assert [r["id"] for r in b_api.get(URL).json()["data"]["incoming_requests"]] == [req_id]
        assert [r["id"] for r in a_api.get(URL).json()["data"]["outgoing_requests"]] == [req_id]
        assert b_api.get(URL).json()["data"]["incoming_requests"][0]["layout_options"] == ["ours", "theirs"]

        resp = b_api.post(URL + f"requests/{req_id}/accept/", {"layout": "theirs"}, format="json")
        assert resp.status_code == 200, resp.json()
        assert resp.json()["sync"]["created"] == 2
        assert resp.json()["data"]["venue"]["name"] == "Food Hall"
        assert sorted(
            Table.objects.filter(restaurant=b, venue_table__isnull=False).values_list("number", flat=True)
        ) == ["1", "2", "3"]
        _pii_free(resp.json())

    @pytest.mark.parametrize("slug", ["nope", "SELF"])
    def test_unknown_and_self_slug_identical_400(self, a_api, a, slug):
        resp = a_api.post(URL + "requests/", {"to_restaurant": a.slug if slug == "SELF" else slug}, format="json")
        assert resp.status_code == 400 and resp.json()["error"]["message"] == "Restaurant not found."

    def test_accept_not_addressed_to_me_is_uniform_404(self, pending_request, a_api, rival_api):
        import uuid

        for client in (a_api, rival_api):
            resp = client.post(URL + f"requests/{pending_request.pk}/accept/", {"layout": "ours"}, format="json")
            assert resp.status_code == 404
            assert resp.json() == rival_api.post(URL + f"requests/{uuid.uuid4()}/accept/", {}, format="json").json()

    def test_decline_and_cancel_scopes(self, pending_request, a_api, b_api, rival_api):
        rid = pending_request.pk
        assert rival_api.post(URL + f"requests/{rid}/decline/").status_code == 404
        assert b_api.post(URL + f"requests/{rid}/cancel/").status_code == 404
        assert a_api.post(URL + f"requests/{rid}/cancel/").status_code == 200
        pending_request.refresh_from_db()
        assert pending_request.status == "cancelled"

    def test_waiter_is_blocked_like_the_rest_of_the_dashboard(self, a, a_waiter, b, pending_request):
        from .conftest import _api

        client = _api(a_waiter, a)
        assert client.get(URL).status_code == 403
        assert client.post(URL + "requests/", {"to_restaurant": b.slug}, format="json").status_code == 403
        assert (
            client.post(URL + f"requests/{pending_request.pk}/accept/", {"layout": "ours"}, format="json").status_code
            == 403
        )

    def test_leave_requires_confirm(self, venue_pair, b_api, b):
        assert b_api.post(URL + "leave/", {"confirm": False}, format="json").status_code == 400
        resp = b_api.post(URL + "leave/", {"confirm": True}, format="json")
        assert resp.status_code == 200 and resp.json()["data"]["venue"] is None
        assert not VenueMember.objects.filter(restaurant=b).exists()


@pytest.mark.django_db
class TestRegistry:
    def test_list_create_patch_deactivate(self, venue_pair, a_api, a, b):
        listed = a_api.get(URL + "tables/").json()["data"]
        assert [t["number"] for t in listed] == ["1", "2", "3"]
        assert listed[0]["local_table_id"] == str(Table.objects.get(restaurant=a, number="1").pk)
        assert listed[0]["qr_url"].startswith("https://example.test/venue/")

        resp = a_api.post(URL + "tables/", {"number": "15", "capacity": 6}, format="json")
        assert resp.status_code == 201, resp.json()
        assert Table.objects.filter(number="15", venue_table__isnull=False).count() == 2

        vt_id = resp.json()["data"]["id"]
        assert a_api.patch(URL + f"tables/{vt_id}/", {"capacity": 2}, format="json").status_code == 200
        assert Table.objects.get(restaurant=b, number="15").capacity == 2

        resp = a_api.post(URL + "tables/", {"number": "9"}, format="json")  # B's private table
        assert resp.status_code == 409 and resp.json()["error"]["code"] == "number_conflict"

        assert a_api.post(URL + f"tables/{vt_id}/deactivate/").status_code == 200
        assert Table.objects.get(restaurant=b, number="15").is_active is False

    def test_registry_is_hidden_from_non_members(self, venue_pair, rival_api):
        assert rival_api.get(URL + "tables/").status_code == 404

    def test_shared_table_locked_in_tables_api(self, venue_pair, b_api, b):
        mirror = Table.objects.get(restaurant=b, number="1")
        url = f"/api/v1/dashboard/tables/{mirror.pk}/"
        assert b_api.get(url).json()["is_shared"] is True
        assert b_api.patch(url, {"number": "77"}, format="json").status_code == 400
        assert b_api.patch(url, {"status": "occupied", "position_x": 3}, format="json").status_code == 200
        assert b_api.delete(url).status_code == 400
        # private tables are still fully editable / creatable
        assert (
            b_api.post("/api/v1/dashboard/tables/", {"number": "42", "capacity": 2}, format="json").status_code == 201
        )


@pytest.mark.django_db
def test_isolation_after_sharing(venue_pair, a, b, a_api, b_api, a_manager, create_staff_member):
    from apps.orders.models import Order
    from apps.tables.models import TableSession

    a_table = Table.objects.get(restaurant=a, number="1")
    session = TableSession.objects.create(table=a_table)
    order = Order.objects.create(restaurant=a, table=a_table, table_session=session, order_type="dine_in")

    for path in (
        "/api/v1/dashboard/orders/",
        "/api/v1/dashboard/tables/sessions/",
        "/api/v1/dashboard/staff/",
        "/api/v1/dashboard/tables/",
    ):
        body = b_api.get(path).content.decode()
        assert str(order.pk) not in body and str(session.pk) not in body and str(a_table.pk) not in body, path
        assert a_manager.email not in body, path


@pytest.mark.django_db
def test_me_restaurants_lists_owned_and_memberships(api_client, venue_pair, a, b, b_manager, create_staff_member):
    from apps.staff.models import StaffRole

    bar_role = next(
        r for r in StaffRole.objects.filter(restaurant=a) or StaffRole.create_default_roles(a) if r.name == "waiter"
    )
    create_staff_member(user=b_manager, restaurant=a, role=bar_role)
    api_client.force_authenticate(b_manager)
    rows = api_client.get("/api/v1/users/me/restaurants/").json()["data"]
    assert {(r["slug"], r["role"], r["is_owner"]) for r in rows} == {
        (a.slug, "waiter", False),
        (b.slug, "manager", False),
    }
    assert all(r["venue"]["slug"] == venue_pair.slug for r in rows)
    api_client.force_authenticate(None)
    assert api_client.get("/api/v1/users/me/restaurants/").status_code == 401
