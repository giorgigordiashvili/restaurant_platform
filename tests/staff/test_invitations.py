"""Invitations: admin + API share one service; email; accept gives admin access."""

from django.core import mail
from django.test import Client

import pytest

from apps.staff import services
from apps.staff.models import StaffInvitation, StaffMember

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _tenant_domain(settings):
    settings.MAIN_DOMAIN = "localhost"
    settings.FRONTEND_BASE_URL = "https://example.test"
    settings.ADMIN_DOMAIN = "admin.example.test"


@pytest.fixture
def owner_admin(user, restaurant, staff_roles, create_staff_member):
    create_staff_member(user=user, restaurant=restaurant, role=next(r for r in staff_roles if r.name == "owner"))
    client = Client(HTTP_HOST=f"{restaurant.slug}.localhost")
    client.force_login(user)
    return client


def test_admin_invite_sends_email(owner_admin, restaurant, staff_roles):
    waiter = next(r for r in staff_roles if r.name == "waiter")
    resp = owner_admin.post(
        "/tenant-admin/staff/staffinvitation/add/",
        {"email": "New.Cook@Example.com", "role": str(waiter.pk), "message": "Welcome!"},
    )
    assert resp.status_code == 302, resp.content[:500]
    inv = StaffInvitation.objects.get()
    assert inv.email == "new.cook@example.com" and inv.status == "pending" and inv.invited_by_id
    assert len(mail.outbox) == 1
    msg = mail.outbox[0]
    assert msg.to == ["new.cook@example.com"]
    assert f"https://example.test/staff/accept/{inv.token}" in msg.body
    assert any("text/html" in alt[1] for alt in msg.alternatives)
    assert "Welcome!" in msg.body


def test_invite_rejects_existing_member(owner_admin, restaurant, waiter_staff, staff_roles):
    waiter = next(r for r in staff_roles if r.name == "waiter")
    with pytest.raises(services.InviteError):
        services.invite(restaurant, waiter_staff.user.email, waiter, invited_by=None)
    resp = owner_admin.post(
        "/tenant-admin/staff/staffinvitation/add/", {"email": waiter_staff.user.email, "role": str(waiter.pk)}
    )
    assert resp.status_code == 403
    assert StaffInvitation.objects.count() == 0


def test_api_invite_uses_service(authenticated_owner_client, restaurant, staff_roles):
    waiter = next(r for r in staff_roles if r.name == "waiter")
    authenticated_owner_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
    resp = authenticated_owner_client.post(
        "/api/v1/dashboard/staff/invite/", {"email": "api@example.com", "role_id": str(waiter.pk)}, format="json"
    )
    assert resp.status_code == 201, resp.content
    assert len(mail.outbox) == 1 and "/staff/accept/" in mail.outbox[0].body


def test_accept_grants_admin_access(restaurant, staff_roles, create_user, user):
    waiter = next(r for r in staff_roles if r.name == "waiter")
    inv = services.invite(restaurant, "hired@example.com", waiter, invited_by=user)
    hired = create_user(email="hired@example.com")
    assert hired.is_staff is False
    from rest_framework.test import APIClient

    client = APIClient()
    client.force_authenticate(hired)
    detail = client.get(f"/api/v1/staff/invitations/{inv.token}/").json()
    data = detail.get("data") or detail
    assert data["restaurant_slug"] == restaurant.slug and data["is_valid"] is True
    assert data["admin_url"].startswith(f"https://{restaurant.slug}.admin.example.test/")
    resp = client.post("/api/v1/staff/invitations/accept/", {"token": inv.token}, format="json")
    assert resp.status_code == 200, resp.content
    hired.refresh_from_db()
    assert hired.is_staff is True
    assert StaffMember.objects.filter(user=hired, restaurant=restaurant, role=waiter, is_active=True).exists()
    admin = Client(HTTP_HOST=f"{restaurant.slug}.localhost")
    admin.force_login(hired)
    assert admin.get("/tenant-admin/").status_code == 200


def test_resend_and_cancel(owner_admin, restaurant, staff_roles, user):
    waiter = next(r for r in staff_roles if r.name == "waiter")
    inv = services.invite(restaurant, "again@example.com", waiter, invited_by=user)
    old_token = inv.token
    resp = owner_admin.post(
        "/tenant-admin/staff/staffinvitation/",
        {"action": "resend_invitations", "_selected_action": [str(inv.pk)]},
        follow=True,
    )
    assert resp.status_code == 200
    inv.refresh_from_db()
    assert inv.token != old_token and len(mail.outbox) == 2
    owner_admin.post(
        "/tenant-admin/staff/staffinvitation/",
        {"action": "cancel_invitations", "_selected_action": [str(inv.pk)]},
        follow=True,
    )
    inv.refresh_from_db()
    assert inv.status == "cancelled"
    assert owner_admin.get(f"/tenant-admin/staff/staffinvitation/{inv.pk}/change/").status_code == 200  # read-only view
