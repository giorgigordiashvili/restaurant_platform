"""Waitlist: queue positions, estimates, notify SMS, seating into a session, expiry, public join, API, admin, report."""

from datetime import timedelta
from decimal import Decimal

from django.test import Client
from django.utils import timezone

from rest_framework.test import APIClient

import pytest

from apps.notifications.models import Notification, OutboundMessage
from apps.tables.models import TableSession
from apps.waitlist import services
from apps.waitlist.models import WaitlistEntry

D = "/api/v1/dashboard/waitlist/"


@pytest.fixture(autouse=True)
def _env(settings):
    settings.MAIN_DOMAIN = "localhost"
    settings.SMS_PROVIDER = "console"
    settings.FRONTEND_BASE_URL = "https://aimenu.ge"


@pytest.fixture
def waitlist(restaurant):
    restaurant.waitlist_enabled = True
    restaurant.tables_enabled = True
    restaurant.save()
    from apps.notifications import services as notifications

    cfg = notifications.settings_for(restaurant)
    cfg.guest_sms = True
    cfg.save()
    return restaurant


@pytest.fixture
def tables(waitlist, create_table, table_section):
    return [
        create_table(restaurant=waitlist, number="W1", section=table_section, capacity=2),
        create_table(restaurant=waitlist, number="W2", section=table_section, capacity=4),
    ]


@pytest.fixture
def owner_api(authenticated_owner_client, restaurant):
    authenticated_owner_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
    return authenticated_owner_client


@pytest.fixture
def owner_admin(user, restaurant, staff_roles, create_staff_member):
    create_staff_member(user=user, restaurant=restaurant, role=next(r for r in staff_roles if r.name == "owner"))
    client = Client(HTTP_HOST=f"{restaurant.slug}.localhost")
    client.force_login(user)
    return client


@pytest.mark.django_db
class TestQueue:
    def test_add_positions_and_messages(self, waitlist, tables, user):
        a = services.add_entry(waitlist, name="Ana", phone="+995555000001", party_size=2, by=user)
        b = services.add_entry(waitlist, name="Beka", phone="+995555000002", party_size=4, by=user)
        assert (a.position, b.position) == (1, 2) and a.quoted_minutes >= 5
        assert OutboundMessage.objects.filter(
            kind="waitlist_joined", to="+995555000001", body__contains="/w/status/"
        ).exists()
        services.reorder(b, 1)
        a.refresh_from_db()
        b.refresh_from_db()
        assert (b.position, a.position) == (1, 2)
        with pytest.raises(services.WaitlistError) as exc:
            services.add_entry(waitlist, name="", party_size=2)
        assert exc.value.code == "name_required"

    def test_notify_seat_and_session(self, waitlist, tables, user):
        e = services.add_entry(waitlist, name="Ana", phone="+995555000001", party_size=3, by=user)
        services.notify_ready(e, by=user)
        assert e.status == "notified" and e.notify_count == 1
        assert OutboundMessage.objects.filter(kind="table_ready", body__contains="Ana").exists()
        session = services.seat(e, tables[1], by=user)
        e.refresh_from_db()
        assert e.status == "seated" and e.session_id == session.pk and e.table_id == tables[1].pk
        assert session.guest_count == 3 and TableSession.objects.get(pk=session.pk).status == "active"
        tables[1].refresh_from_db()
        assert tables[1].status == "occupied"
        with pytest.raises(services.WaitlistError):
            services.seat(e, tables[0], by=user)

    def test_expire_and_daily_close(self, waitlist, tables, user):
        e = services.add_entry(waitlist, name="Late", party_size=2, by=user)
        services.notify_ready(e, by=user)
        WaitlistEntry.objects.filter(pk=e.pk).update(notified_at=timezone.now() - timedelta(minutes=30))
        assert services.auto_expire() == 1
        e.refresh_from_db()
        assert e.status == "no_show"
        old = services.add_entry(waitlist, name="Yesterday", party_size=2, by=user)
        WaitlistEntry.objects.filter(pk=old.pk).update(date=services.today(waitlist) - timedelta(days=1))
        assert services.daily_close() == 1
        assert WaitlistEntry.objects.get(pk=old.pk).status == "left"

    def test_from_reservation_and_report(self, waitlist, tables, user, create_reservation):
        r = create_reservation(restaurant=waitlist, party_size=2)
        r.status = "waitlist"
        r.save()
        e = services.from_reservation(r, by=user)
        assert e.reservation_id == r.pk and e.source == "reservation"
        assert services.from_reservation(r, by=user).pk == e.pk
        services.seat(e, tables[0], by=user)
        r.refresh_from_db()
        assert r.status == "seated" and r.table_id == tables[0].pk
        rep = services.report(waitlist, services.today(waitlist), services.today(waitlist))
        assert rep["walk_ins"] == 1 and rep["seated"] == 1 and rep["abandon_rate"] == 0

    def test_estimate_uses_history(self, waitlist, tables, user):
        for _ in range(4):
            s = TableSession.objects.create(table=tables[1], status="closed", guest_count=2)
            TableSession.objects.filter(pk=s.pk).update(
                started_at=timezone.now() - timedelta(minutes=90), closed_at=timezone.now() - timedelta(minutes=30)
            )
        assert services.turn_minutes(waitlist, 2) == 60
        assert services.estimate_wait(waitlist, 2) >= 5


@pytest.mark.django_db
class TestApi:
    def test_dashboard_flow(self, waitlist, tables, owner_api):
        res = owner_api.post(D + "entries/", {"name": "Ana", "phone": "555000001", "party_size": 2}, format="json")
        assert res.status_code == 201, res.content
        eid = res.data["id"]
        assert owner_api.get(D + "entries/").data[0]["name"] == "Ana"
        assert owner_api.get(D + "estimate/?party_size=4").data["minutes"] >= 5
        assert (
            owner_api.patch(D + f"entries/{eid}/", {"party_size": 3, "notes": "stroller"}, format="json").data[
                "party_size"
            ]
            == 3
        )
        assert owner_api.post(D + f"entries/{eid}/notify/").data["status"] == "notified"
        res = owner_api.post(D + f"entries/{eid}/seat/", {"table_id": str(tables[1].pk)}, format="json")
        assert res.status_code == 200 and res.data["status"] == "seated" and res.data["table_number"] == "W2"
        assert owner_api.get(D + "summary/").data["seated_today"] == 1
        res = owner_api.post(D + "entries/", {"name": "Gone", "party_size": 2}, format="json")
        assert owner_api.post(D + f"entries/{res.data['id']}/left/").data["status"] == "left"
        cfg = owner_api.patch(D + "settings/", {"default_wait_minutes": 20}, format="json")
        assert cfg.status_code == 200 and cfg.data["default_wait_minutes"] == 20 and "/w/" in cfg.data["join_url"]

    def test_public_join_and_status(self, waitlist, tables, django_capture_on_commit_callbacks):
        cfg = services.settings_for(waitlist)
        url = f"/api/v1/waitlist/{waitlist.slug}/{cfg.join_token}/"
        info = APIClient().get(url).json()["data"]
        assert info["open"] and info["waiting"] == 0
        assert APIClient().get(f"/api/v1/waitlist/{waitlist.slug}/wrong/").status_code == 404
        with django_capture_on_commit_callbacks(execute=True):
            res = APIClient().post(url, {"name": "Gio", "phone": "+995555000009", "party_size": 2}, format="json")
        assert res.status_code == 201, res.content
        token = res.json()["data"]["token"]
        assert Notification.objects.filter(title__startswith="Waitlist: Gio").exists()
        res = APIClient().post(url, {"name": "Gio", "phone": "+995555000009", "party_size": 2}, format="json")
        assert res.status_code == 400 and res.json()["error"]["code"] == "already_waiting"
        st = APIClient().get(f"/api/v1/waitlist/status/{token}/").json()["data"]
        assert st["position"] == 1 and st["ahead"] == 0 and st["status"] == "waiting"
        assert APIClient().delete(f"/api/v1/waitlist/status/{token}/").json()["data"]["status"] == "left"
        cfg.allow_self_join = False
        cfg.save()
        res = APIClient().post(url, {"name": "X", "phone": "+995555000010", "party_size": 2}, format="json")
        assert res.status_code == 400 and res.json()["error"]["code"] == "self_join_off"

    def test_module_off(self, restaurant, owner_api):
        assert owner_api.get(D + "entries/").status_code == 403

    def test_admin_pages(self, waitlist, tables, owner_admin, user):
        html = owner_admin.get("/tenant-admin/waitlist/waitlistsettingspage/").content.decode()
        assert "data:image/png;base64" in html and "/w/" in html
        assert (
            owner_admin.post(
                "/tenant-admin/waitlist/waitlistsettingspage/save/",
                {
                    "default_wait_minutes": "25",
                    "allow_self_join": "1",
                    "sms_on_ready": "1",
                    "notify_expire_minutes": "8",
                    "max_party_size": "10",
                },
            ).status_code
            == 302
        )
        assert services.settings_for(waitlist).default_wait_minutes == 25
        e = services.add_entry(waitlist, name="Ana", party_size=2, by=user)
        html = owner_admin.get("/tenant-admin/waitlist/waitlistentry/").content.decode()
        assert "Ana" in html and 'data-testid="waitlist-summary"' in html
        assert owner_admin.get(f"/tenant-admin/waitlist/waitlistentry/{e.pk}/notify/").status_code == 302
        html = owner_admin.get(f"/tenant-admin/waitlist/waitlistentry/{e.pk}/seat-page/").content.decode()
        assert "W2" in html
        assert (
            owner_admin.post(
                f"/tenant-admin/waitlist/waitlistentry/{e.pk}/seat-page/", {"table_id": str(tables[0].pk)}
            ).status_code
            == 302
        )
        e.refresh_from_db()
        assert e.status == "seated"
        assert "Waitlist" in owner_admin.get("/tenant-admin/").content.decode()
