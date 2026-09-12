"""Module registry, switching rules and the modules field on the API."""

import pytest

from apps.audit.models import AuditLog
from apps.core import modules
from apps.core.admin_sites import tenant_admin_site
from apps.tenants.models import Restaurant

pytestmark = pytest.mark.django_db


def test_registry_matches_the_model():
    field_names = {f.name for f in Restaurant._meta.fields}
    registered = {(m._meta.app_label, m._meta.model_name) for m in tenant_admin_site._registry}
    for m in modules.MODULES:
        for name in (m.flag, *m.sub_flags, *m.sub_fields):
            if name:
                assert name in field_names, f"{m.code}: {name} is not a Restaurant field"
        for target in m.model_names:
            assert target in registered, f"{m.code}: {target} is not registered on the tenant admin"
        for dep in (*m.requires, *m.recommends):
            assert dep in modules.MODULES_BY_CODE
    assert set(modules.modules_dict(Restaurant())) == set(modules.CODES)


def test_defaults_keep_existing_tenants_unchanged(restaurant):
    on = modules.modules_dict(restaurant)
    assert on["menu"] and on["ordering"] and on["tables"] and on["reservations"] and on["kitchen"]
    assert on["loyalty"] and on["reviews"]
    assert not on["warehouse"] and not on["payments"]


def test_payments_is_derived_from_providers(restaurant):
    assert modules.is_enabled(restaurant, "payments") is False
    restaurant.accepts_flitt_payments = True
    assert modules.is_enabled(restaurant, "payments") is True


def test_kitchen_depends_on_ordering(restaurant, user):
    with pytest.raises(modules.ModuleError):
        modules.set_module(restaurant, "ordering", False, by=user)  # kitchen still on
    modules.set_module(restaurant, "kitchen", False, by=user)
    modules.set_module(restaurant, "ordering", False, by=user)
    restaurant.refresh_from_db()
    assert not restaurant.accepts_remote_orders and not restaurant.kitchen_enabled
    with pytest.raises(modules.ModuleError):
        modules.set_module(restaurant, "kitchen", True, by=user)


def test_set_module_runs_hooks_and_audits(restaurant, user, monkeypatch):
    calls = []
    monkeypatch.setattr("apps.inventory.hooks.on_feature_toggled", lambda r, enabled, by=None: calls.append(enabled))
    assert modules.set_module(restaurant, "warehouse", True, by=user) is True
    assert modules.set_module(restaurant, "warehouse", True, by=user) is False  # no-op
    assert calls == [True]
    log = AuditLog.objects.filter(restaurant=restaurant, action="settings_update").latest("created_at")
    assert log.changes == {"warehouse_enabled": True}
    assert log.user == user


def test_reservations_hook_mirrors_settings(restaurant, user):
    from apps.reservations.models import ReservationSettings

    ReservationSettings.objects.create(restaurant=restaurant)
    modules.set_module(restaurant, "reservations", False, by=user)
    assert ReservationSettings.objects.get(restaurant=restaurant).accepts_reservations is False


def test_set_options_validates_payment_ids(restaurant, user):
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError):
        modules.set_options(restaurant, "payments", {"accepts_bog_payments": True, "bog_payout_iban": ""}, by=user)
    restaurant.refresh_from_db()
    assert restaurant.accepts_bog_payments is False
    modules.set_options(restaurant, "ordering", {"accepts_takeaway": False}, by=user)
    restaurant.refresh_from_db()
    assert restaurant.accepts_takeaway is False


def test_resources_follow_enabled_modules(restaurant, user):
    assert "warehouse" not in modules.resources_available(restaurant)
    modules.set_module(restaurant, "warehouse", True, by=user)
    available = modules.resources_available(restaurant)
    assert "warehouse" in available and "warehouse_logs" in available
    assert available[-3:] == ["staff", "settings", "analytics"]
    modules.set_module(restaurant, "tables", False, by=user)
    assert "tables" not in modules.resources_available(restaurant)
    assert ("tables", "table") in modules.hidden_models(restaurant)
    assert "venues" in modules.hidden_apps(restaurant)


def test_public_api_exposes_modules(api_client, restaurant):
    body = api_client.get(f"/api/v1/restaurants/{restaurant.slug}/").json()
    data = body.get("data") or body
    assert data["modules"]["ordering"] is True and data["modules"]["warehouse"] is False
    listing = api_client.get("/api/v1/restaurants/").json()
    rows = (listing.get("data") or listing)["results"]
    assert "modules" in rows[0] and "warehouse_enabled" not in rows[0]


def test_settings_api_goes_through_the_registry(authenticated_owner_client, restaurant):
    authenticated_owner_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
    resp = authenticated_owner_client.patch(
        "/api/v1/dashboard/settings/", {"accepts_remote_orders": False}, format="json"
    )
    assert resp.status_code == 400  # kitchen still on
    resp = authenticated_owner_client.patch(
        "/api/v1/dashboard/settings/", {"accepts_remote_orders": False, "kitchen_enabled": False}, format="json"
    )
    assert resp.status_code == 200, resp.content
    restaurant.refresh_from_db()
    assert restaurant.accepts_remote_orders is False and restaurant.kitchen_enabled is False
    assert AuditLog.objects.filter(restaurant=restaurant, action="settings_update").count() >= 2


def test_my_restaurants_carries_modules(authenticated_owner_client, restaurant):
    body = authenticated_owner_client.get("/api/v1/users/me/restaurants/").json()
    rows = body.get("data") or body
    assert rows[0]["modules"]["kitchen"] is True
