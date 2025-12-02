"""
Tests for multi-tenant admin functionality.
"""

import pytest
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory

from apps.core.admin import (
    ExportMixin,
    ReadOnlyAdminMixin,
    SuperadminOnlyMixin,
    TenantAwareModelAdmin,
    TenantSimulatorMixin,
    make_active,
    make_inactive,
)
from apps.orders.models import Order
from apps.staff.models import StaffMember, StaffRole
from apps.tables.models import Table


@pytest.fixture
def request_factory():
    """Return a request factory."""
    return RequestFactory()


@pytest.fixture
def superuser_request(request_factory, admin_user):
    """Create a request with superuser."""
    request = request_factory.get("/admin/")
    request.user = admin_user
    request.session = {}
    return request


@pytest.fixture
def staff_user_request(request_factory, manager_user, restaurant, staff_roles, create_staff_member):
    """Create a request with a staff user."""
    manager_role = next(r for r in staff_roles if r.name == "manager")
    create_staff_member(user=manager_user, restaurant=restaurant, role=manager_role)
    request = request_factory.get("/admin/")
    request.user = manager_user
    request.session = {}
    return request


@pytest.mark.django_db
class TestTenantAwareModelAdmin:
    """Tests for TenantAwareModelAdmin."""

    def test_superuser_sees_all_data(self, superuser_request, restaurant, another_restaurant, create_table):
        """Test that superusers see data from all restaurants."""
        # Create tables in both restaurants
        create_table(restaurant=restaurant, number="T1")
        create_table(restaurant=another_restaurant, number="T2")

        admin = TenantAwareModelAdmin(Table, AdminSite())
        qs = admin.get_queryset(superuser_request)

        assert qs.count() == 2

    def test_superuser_with_simulation_sees_filtered_data(
        self, superuser_request, restaurant, another_restaurant, create_table
    ):
        """Test that superusers with simulation see only simulated restaurant's data."""
        t1 = create_table(restaurant=restaurant, number="T1")
        create_table(restaurant=another_restaurant, number="T2")

        # Simulate restaurant
        superuser_request.session["admin_simulated_restaurant"] = str(restaurant.id)

        admin = TenantAwareModelAdmin(Table, AdminSite())
        qs = admin.get_queryset(superuser_request)

        assert qs.count() == 1
        assert qs.first() == t1

    def test_staff_sees_only_their_restaurant(self, staff_user_request, restaurant, another_restaurant, create_table):
        """Test that staff only see their restaurant's data."""
        t1 = create_table(restaurant=restaurant, number="T1")
        create_table(restaurant=another_restaurant, number="T2")

        admin = TenantAwareModelAdmin(Table, AdminSite())
        qs = admin.get_queryset(staff_user_request)

        assert qs.count() == 1
        assert qs.first() == t1

    def test_superuser_gets_restaurant_filter(self, superuser_request):
        """Test that superusers get restaurant filter in list_filter."""
        admin = TenantAwareModelAdmin(Table, AdminSite())
        admin.list_filter = ["status"]

        filters = admin.get_list_filter(superuser_request)

        assert "restaurant" in filters
        assert filters[0] == "restaurant"  # Should be first

    def test_staff_does_not_get_restaurant_filter(self, staff_user_request):
        """Test that staff don't get restaurant filter added."""
        admin = TenantAwareModelAdmin(Table, AdminSite())
        admin.list_filter = ["status"]

        filters = admin.get_list_filter(staff_user_request)

        # Restaurant filter should not be automatically added for staff
        assert "restaurant" not in filters


@pytest.mark.django_db
class TestSuperadminOnlyMixin:
    """Tests for SuperadminOnlyMixin."""

    def test_superuser_has_all_permissions(self, superuser_request):
        """Test that superusers have all permissions."""

        class TestAdmin(SuperadminOnlyMixin, TenantAwareModelAdmin):
            pass

        admin = TestAdmin(Table, AdminSite())

        assert admin.has_module_permission(superuser_request) is True
        assert admin.has_view_permission(superuser_request) is True
        assert admin.has_add_permission(superuser_request) is True
        assert admin.has_change_permission(superuser_request) is True
        assert admin.has_delete_permission(superuser_request) is True

    def test_non_superuser_has_no_permissions(self, staff_user_request):
        """Test that non-superusers have no permissions."""

        class TestAdmin(SuperadminOnlyMixin, TenantAwareModelAdmin):
            pass

        admin = TestAdmin(Table, AdminSite())

        assert admin.has_module_permission(staff_user_request) is False
        assert admin.has_view_permission(staff_user_request) is False
        assert admin.has_add_permission(staff_user_request) is False
        assert admin.has_change_permission(staff_user_request) is False
        assert admin.has_delete_permission(staff_user_request) is False


@pytest.mark.django_db
class TestReadOnlyAdminMixin:
    """Tests for ReadOnlyAdminMixin."""

    def test_readonly_mixin_prevents_changes(self, superuser_request):
        """Test that readonly mixin prevents add/change/delete."""

        class TestAdmin(ReadOnlyAdminMixin, TenantAwareModelAdmin):
            pass

        admin = TestAdmin(Table, AdminSite())

        assert admin.has_add_permission(superuser_request) is False
        assert admin.has_change_permission(superuser_request) is False
        assert admin.has_delete_permission(superuser_request) is False


@pytest.mark.django_db
class TestExportMixin:
    """Tests for ExportMixin."""

    def test_export_csv(self, superuser_request, restaurant, create_table):
        """Test CSV export action."""
        t1 = create_table(restaurant=restaurant, number="T1")
        t2 = create_table(restaurant=restaurant, number="T2")

        admin = TenantAwareModelAdmin(Table, AdminSite())
        queryset = Table.objects.filter(restaurant=restaurant)

        response = admin.export_as_csv(superuser_request, queryset)

        assert response.status_code == 200
        assert response["Content-Type"] == "text/csv"
        assert "attachment" in response["Content-Disposition"]
        assert ".csv" in response["Content-Disposition"]

    def test_export_json(self, superuser_request, restaurant, create_table):
        """Test JSON export action."""
        t1 = create_table(restaurant=restaurant, number="T1")

        admin = TenantAwareModelAdmin(Table, AdminSite())
        queryset = Table.objects.filter(restaurant=restaurant)

        response = admin.export_as_json(superuser_request, queryset)

        assert response.status_code == 200
        assert response["Content-Type"] == "application/json"
        assert "attachment" in response["Content-Disposition"]
        assert ".json" in response["Content-Disposition"]


@pytest.mark.django_db
class TestBulkActions:
    """Tests for bulk action helpers."""

    def test_make_active(self, superuser_request, restaurant, create_table):
        """Test make_active bulk action."""
        t1 = create_table(restaurant=restaurant, number="T1", is_active=False)
        t2 = create_table(restaurant=restaurant, number="T2", is_active=False)

        class MockModelAdmin:
            def message_user(self, request, message):
                pass

        queryset = Table.objects.filter(restaurant=restaurant)
        make_active(MockModelAdmin(), superuser_request, queryset)

        t1.refresh_from_db()
        t2.refresh_from_db()
        assert t1.is_active is True
        assert t2.is_active is True

    def test_make_inactive(self, superuser_request, restaurant, create_table):
        """Test make_inactive bulk action."""
        t1 = create_table(restaurant=restaurant, number="T1", is_active=True)
        t2 = create_table(restaurant=restaurant, number="T2", is_active=True)

        class MockModelAdmin:
            def message_user(self, request, message):
                pass

        queryset = Table.objects.filter(restaurant=restaurant)
        make_inactive(MockModelAdmin(), superuser_request, queryset)

        t1.refresh_from_db()
        t2.refresh_from_db()
        assert t1.is_active is False
        assert t2.is_active is False


@pytest.mark.django_db
class TestTenantSimulation:
    """Tests for tenant simulation feature."""

    def test_simulation_stores_in_session(self, superuser_request, restaurant):
        """Test that simulation ID is stored in session."""
        superuser_request.session["admin_simulated_restaurant"] = str(restaurant.id)

        assert superuser_request.session.get("admin_simulated_restaurant") == str(restaurant.id)

    def test_clearing_simulation(self, superuser_request, restaurant):
        """Test clearing simulation from session."""
        superuser_request.session["admin_simulated_restaurant"] = str(restaurant.id)
        superuser_request.session.pop("admin_simulated_restaurant", None)

        assert superuser_request.session.get("admin_simulated_restaurant") is None


@pytest.mark.django_db
class TestNestedTenantField:
    """Tests for nested tenant field relationships."""

    def test_nested_tenant_field_filters_correctly(self, superuser_request, restaurant, create_table):
        """Test filtering with nested tenant field like table__restaurant."""
        from apps.tables.admin import TableQRCodeAdmin
        from apps.tables.models import TableQRCode

        table1 = create_table(restaurant=restaurant, number="T1")
        table2 = create_table(restaurant=restaurant, number="T2")

        # Create QR codes
        qr1 = TableQRCode.objects.create(table=table1, code="qr1")
        qr2 = TableQRCode.objects.create(table=table2, code="qr2")

        # Without simulation, superuser sees all
        admin = TableQRCodeAdmin(TableQRCode, AdminSite())
        qs = admin.get_queryset(superuser_request)

        assert qs.count() == 2

    def test_order_item_nested_filter(self, superuser_request, restaurant, create_table, create_order):
        """Test filtering with deeply nested tenant field like order__restaurant."""
        from apps.orders.admin import OrderItemAdmin
        from apps.orders.models import OrderItem

        table1 = create_table(restaurant=restaurant, number="T1")
        order1 = create_order(restaurant=restaurant, table=table1)

        # Create order items
        OrderItem.objects.create(order=order1, item_name="Item 1", quantity=1, unit_price=10, total_price=10)

        # Simulate restaurant
        superuser_request.session["admin_simulated_restaurant"] = str(restaurant.id)

        admin = OrderItemAdmin(OrderItem, AdminSite())
        qs = admin.get_queryset(superuser_request)

        assert qs.count() == 1


@pytest.mark.django_db
class TestAdminSiteCustomization:
    """Tests for admin site customization."""

    def test_admin_site_headers_are_set(self):
        """Test that admin site headers are customized."""
        from django.contrib import admin

        assert admin.site.site_header == "Restaurant Platform Admin"
        assert admin.site.site_title == "Restaurant Platform"
        assert admin.site.index_title == "Platform Administration"
