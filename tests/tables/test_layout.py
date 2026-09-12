"""Floor plan: bulk layout endpoint and role-based table permissions."""

import pytest

from apps.tables.models import Table


@pytest.mark.django_db
class TestLayout:
    def test_bulk_layout_update(self, authenticated_owner_client, restaurant, create_table, table_section):
        t1 = create_table(restaurant=restaurant, number="A1", section=table_section)
        t2 = create_table(restaurant=restaurant, number="A2", section=table_section, shape="round")
        c = authenticated_owner_client
        c.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        res = c.patch(
            "/api/v1/dashboard/tables/layout/",
            {
                "tables": [
                    {"id": str(t1.id), "position_x": 100, "position_y": 50, "rotation": 90, "width": 160, "height": 80},
                    {"id": str(t2.id), "position_x": 400, "position_y": 300},
                ]
            },
            format="json",
        )
        assert res.status_code == 200, res.content
        t1.refresh_from_db()
        t2.refresh_from_db()
        assert (t1.position_x, t1.position_y, t1.rotation, t1.width, t1.height) == (100, 50, 90, 160, 80)
        assert (t2.position_x, t2.position_y, t2.rotation, t2.width) == (400, 300, 0, 100)
        assert t2.shape == "round"  # locked/other fields untouched
        rows = res.json().get("data") or res.json()
        assert {r["number"] for r in rows} == {"A1", "A2"} and rows[0]["width"]

    def test_scoped_to_restaurant(self, authenticated_owner_client, restaurant, another_restaurant, create_table):
        foreign = create_table(restaurant=another_restaurant, number="Z")
        c = authenticated_owner_client
        c.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        res = c.patch(
            "/api/v1/dashboard/tables/layout/",
            {"tables": [{"id": str(foreign.id), "position_x": 1, "position_y": 1}]},
            format="json",
        )
        assert res.status_code == 404
        foreign.refresh_from_db()
        assert foreign.position_x is None

    def test_waiter_can_read_and_move_but_not_create(
        self, authenticated_waiter_client, waiter_staff, restaurant, create_table, table_section
    ):
        t = create_table(restaurant=restaurant, number="B1", section=table_section)
        c = authenticated_waiter_client
        c.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        assert c.get("/api/v1/dashboard/tables/").status_code == 200
        assert c.get("/api/v1/dashboard/tables/sections/").status_code == 200
        res = c.patch(
            "/api/v1/dashboard/tables/layout/",
            {"tables": [{"id": str(t.id), "position_x": 10, "position_y": 10}]},
            format="json",
        )
        assert res.status_code == 200
        assert c.post("/api/v1/dashboard/tables/", {"number": "B2", "capacity": 2}, format="json").status_code == 403
        assert c.delete(f"/api/v1/dashboard/tables/{t.id}/").status_code == 403
        assert Table.objects.filter(pk=t.pk).exists()

    def test_section_floor_fields(self, authenticated_owner_client, restaurant, table_section):
        c = authenticated_owner_client
        c.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        res = c.patch(
            f"/api/v1/dashboard/tables/sections/{table_section.id}/",
            {"floor_width": 1200, "floor_height": 800, "background_note": "Terrace"},
            format="json",
        )
        assert res.status_code == 200, res.content
        table_section.refresh_from_db()
        assert (table_section.floor_width, table_section.floor_height) == (1200, 800)
