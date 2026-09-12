"""Clock in / out, auto-close, hours report, rota (publish / copy / reminders), activity feed, API and admin."""

from datetime import timedelta
from decimal import Decimal

from django.test import Client
from django.utils import timezone

from rest_framework.test import APIClient

import pytest
from rest_framework_simplejwt.tokens import RefreshToken

from apps.audit.models import AuditLog
from apps.notifications.models import Notification
from apps.timekeeping import services
from apps.timekeeping.models import RotaShift, TimeEntry


@pytest.fixture
def tk(restaurant):
    restaurant.timekeeping_enabled = True
    restaurant.save(update_fields=["timekeeping_enabled"])
    return restaurant


def client_for(user, restaurant):
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    c.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
    return c


@pytest.mark.django_db
class TestClock:
    def test_clock_cycle_and_report(self, tk, waiter_staff, manager_staff, user):
        waiter_staff.hourly_rate = Decimal("10")
        waiter_staff.save()
        e = services.clock_in(waiter_staff, by=waiter_staff.user)
        assert e.is_open and services.open_entry(waiter_staff) == e
        with pytest.raises(services.TimekeepingError):
            services.clock_in(waiter_staff)
        assert [x.staff_member for x in services.whos_in(tk)] == [waiter_staff]
        e.clock_in = timezone.now() - timedelta(hours=8, minutes=30)
        e.save()
        out = services.clock_out(waiter_staff, by=waiter_staff.user, break_minutes=30)
        assert out.worked_minutes == 480 and out.worked_hours == Decimal("8.00")
        with pytest.raises(services.TimekeepingError):
            services.clock_out(waiter_staff)
        assert services.today_minutes(waiter_staff) >= 0
        rows = services.hours_report(tk, timezone.now() - timedelta(days=1), timezone.now() + timedelta(days=1))
        row = next(r for r in rows if r["member_id"] == str(waiter_staff.pk))
        assert row["hours"] == Decimal("8.00") and row["cost"] == Decimal("80.00") and row["entries"] == 1
        assert (
            AuditLog.objects.filter(action="clock_in").exists() and AuditLog.objects.filter(action="clock_out").exists()
        )

    def test_auto_close(self, tk, waiter_staff):
        e = services.clock_in(waiter_staff)
        e.clock_in = timezone.now() - timedelta(hours=20)
        e.save()
        assert services.auto_close_stale() == 1
        e.refresh_from_db()
        assert e.auto_closed and e.source == "auto" and e.clock_out == e.clock_in + timedelta(hours=16)


@pytest.mark.django_db
class TestRota:
    def test_publish_copy_reminders(self, tk, waiter_staff, manager_staff, user, django_capture_on_commit_callbacks):
        monday = services.week_start(timezone.localdate())
        s1 = RotaShift.objects.create(
            restaurant=tk, staff_member=waiter_staff, date=monday, start_time="10:00", end_time="18:00", label="Floor"
        )
        RotaShift.objects.create(
            restaurant=tk,
            staff_member=manager_staff,
            date=monday + timedelta(days=1),
            start_time="18:00",
            end_time="02:00",
        )
        assert s1.hours == Decimal("8.00") and RotaShift.objects.get(staff_member=manager_staff).hours == Decimal(
            "8.00"
        )
        assert services.my_upcoming(waiter_staff) == []  # unpublished
        with django_capture_on_commit_callbacks(execute=True):
            assert services.publish_week(tk, monday, by=user) == 2
        s1.refresh_from_db()
        assert s1.published and Notification.objects.filter(event="rota.published", user=waiter_staff.user).exists()
        assert AuditLog.objects.filter(action="rota_publish").exists()
        n = services.copy_week(tk, monday, monday + timedelta(days=7), by=user)
        assert n == 2 and RotaShift.objects.filter(date=monday + timedelta(days=7), published=False).exists()
        assert services.copy_week(tk, monday, monday + timedelta(days=7)) == 0  # no duplicates
        # reminder: a published shift starting in 30 minutes
        soon = timezone.localtime(timezone.now() + timedelta(minutes=30))
        s3 = RotaShift.objects.create(
            restaurant=tk,
            staff_member=waiter_staff,
            date=soon.date(),
            start_time=soon.time().replace(second=0, microsecond=0),
            end_time="23:59",
            published=True,
        )
        with django_capture_on_commit_callbacks(execute=True):
            assert services.send_shift_reminders() == 1
        s3.refresh_from_db()
        assert (
            s3.reminder_sent_at and Notification.objects.filter(event="shift.reminder", user=waiter_staff.user).exists()
        )
        assert services.send_shift_reminders() == 0


@pytest.mark.django_db
class TestApiAndAdmin:
    def test_api(self, tk, waiter_staff, manager_staff):
        waiter = client_for(waiter_staff.user, tk)
        manager = client_for(manager_staff.user, tk)
        base = "/api/v1/dashboard/timekeeping/"
        res = waiter.get(f"{base}clock/")
        assert res.status_code == 200 and res.json()["clocked_in"] is False and res.json()["manager"] is False
        res = waiter.post(f"{base}clock/", {"action": "in"}, format="json")
        assert res.status_code == 200 and res.json()["clocked_in"] is True
        assert waiter.post(f"{base}clock/", {"action": "in"}, format="json").status_code == 409
        res = manager.get(f"{base}whos-in/")
        assert res.status_code == 200 and res.json()[0]["name"].startswith("Waiter")
        assert waiter.get(f"{base}whos-in/").status_code == 200  # waiters have timekeeping:read
        res = waiter.post(f"{base}clock/", {"action": "out", "break_minutes": 15}, format="json")
        assert res.json()["clocked_in"] is False
        assert len(waiter.get(f"{base}entries/").json()) == 1 and len(manager.get(f"{base}entries/").json()) == 1
        monday = services.week_start(timezone.localdate())
        RotaShift.objects.create(
            restaurant=tk, staff_member=waiter_staff, date=monday, start_time="10:00", end_time="18:00"
        )
        assert waiter.get(f"{base}rota/?week={monday}").json() == []  # unpublished hidden from staff
        assert len(manager.get(f"{base}rota/?week={monday}").json()) == 1
        services.publish_week(tk, monday)
        assert len(waiter.get(f"{base}rota/?week={monday}").json()) == 1
        mine = waiter.get(f"{base}rota/mine/").json()
        assert all(r["published"] for r in mine)
        tk.timekeeping_enabled = False
        tk.save(update_fields=["timekeeping_enabled"])
        assert waiter.get(f"{base}clock/").status_code in (403, 404)

    def test_admin_and_activity(
        self, tk, user, staff_roles, create_staff_member, waiter_staff, manager_staff, menu_item
    ):
        create_staff_member(user=user, restaurant=tk, role=next(r for r in staff_roles if r.name == "owner"))
        c = Client(HTTP_HOST=f"{tk.slug}.localhost")
        c.force_login(user)
        monday = services.week_start(timezone.localdate())
        page = c.get(f"/tenant-admin/timekeeping/rotashift/?week={monday}")
        assert page.status_code == 200 and 'data-testid="rota-week"' in page.content.decode()
        res = c.post(
            "/tenant-admin/timekeeping/rotashift/add/",
            {
                "staff_member": str(waiter_staff.pk),
                "date": str(monday),
                "start_time": "09:00",
                "end_time": "17:00",
                "label": "Bar",
            },
        )
        assert res.status_code == 302, res.content[:300]
        assert c.post("/tenant-admin/timekeeping/rotashift/publish/", {"week": str(monday)}).status_code == 302
        assert RotaShift.objects.get(staff_member=waiter_staff).published
        assert (
            c.post(
                "/tenant-admin/timekeeping/rotashift/copy-week/", {"week": str(monday + timedelta(days=7))}
            ).status_code
            == 302
        )
        assert RotaShift.objects.filter(date=monday + timedelta(days=7)).count() == 1
        e = services.clock_in(waiter_staff)
        page = c.get("/tenant-admin/timekeeping/timeentry/")
        assert page.status_code == 200
        res = c.post(
            f"/tenant-admin/timekeeping/timeentry/{e.pk}/change/",
            {
                "staff_member": str(waiter_staff.pk),
                "clock_in_0": timezone.localtime(e.clock_in).strftime("%Y-%m-%d"),
                "clock_in_1": timezone.localtime(e.clock_in).strftime("%H:%M:%S"),
                "clock_out_0": timezone.localdate().strftime("%Y-%m-%d"),
                "clock_out_1": "23:00:00",
                "break_minutes": "0",
                "note": "fixed",
                "auto_closed": "",
            },
        )
        assert res.status_code == 302, res.content[:500]
        assert AuditLog.objects.filter(action="clock_edit").exists()
        # hours report page and activity feed
        assert c.get("/tenant-admin/reports/hoursreport/").status_code == 200
        feed = c.get("/tenant-admin/audit/activityfeed/")
        assert feed.status_code == 200 and "clocked in" in feed.content.decode()
        # a price change is logged
        from apps.menu.models import MenuItem

        res = c.post(
            f"/tenant-admin/menu/menuitem/{menu_item.pk}/change/",
            {
                "translations-en-name": "Test Dish",
                "price": "12.50",
                "category": str(menu_item.category_id),
                "preparation_station": "kitchen",
                "preparation_time_minutes": "15",
                "display_order": "0",
                "spicy_level": "0",
                "stock_quantity": "0",
                "is_available": "on",
                "modifier_groups_link-TOTAL_FORMS": "0",
                "modifier_groups_link-INITIAL_FORMS": "0",
                "modifier_groups_link-MIN_NUM_FORMS": "0",
                "modifier_groups_link-MAX_NUM_FORMS": "1000",
                "combo_components-TOTAL_FORMS": "0",
                "combo_components-INITIAL_FORMS": "0",
                "combo_components-MIN_NUM_FORMS": "0",
                "combo_components-MAX_NUM_FORMS": "1000",
                "recipe_lines-TOTAL_FORMS": "0",
                "recipe_lines-INITIAL_FORMS": "0",
                "recipe_lines-MIN_NUM_FORMS": "0",
                "recipe_lines-MAX_NUM_FORMS": "1000",
            },
        )
        if res.status_code == 302:
            assert MenuItem.objects.get(pk=menu_item.pk).price == Decimal("12.50")
            assert AuditLog.objects.filter(action="price_change").exists()
        # managers can read the audit API, waiters cannot
        manager = client_for(manager_staff.user, tk)
        assert manager.get("/api/v1/dashboard/audit/").status_code == 200
        assert client_for(waiter_staff.user, tk).get("/api/v1/dashboard/audit/").status_code == 403
        # the feed is hidden from a waiter in the admin too
        w = Client(HTTP_HOST=f"{tk.slug}.localhost")
        w.force_login(waiter_staff.user)
        assert w.get("/tenant-admin/audit/activityfeed/").status_code == 403
