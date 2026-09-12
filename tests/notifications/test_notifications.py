"""Staff notifications (recipients, dedupe, Expo push, prefs), guest messages (providers), hooks, API, admin."""

from datetime import time, timedelta

from django.test import Client
from django.utils import timezone

import pytest

from apps.notifications import events, hooks, providers, services
from apps.notifications.models import Device, Notification, OutboundMessage, StaffNotificationPrefs

EXPO_OK = (200, {"data": [{"status": "ok", "id": "t1"}]})


@pytest.mark.django_db
class TestRecipientsAndPush:
    def test_recipients_follow_permissions_and_mutes(
        self, restaurant, user, manager_staff, waiter_staff, kitchen_staff
    ):
        people = {u.pk for u in services.recipients(restaurant, events.get("order.new"))}
        assert user.pk in people and manager_staff.user_id in people and waiter_staff.user_id in people
        people = {u.pk for u in services.recipients(restaurant, events.get("shift.closed"))}
        assert manager_staff.user_id in people and waiter_staff.user_id not in people
        assert kitchen_staff.user_id not in people
        StaffNotificationPrefs.objects.create(
            restaurant=restaurant, user=manager_staff.user, muted_events=["order.new"]
        )
        people = {u.pk for u in services.recipients(restaurant, events.get("order.new"))}
        assert manager_staff.user_id not in people and user.pk in people

    def test_notify_creates_rows_pushes_and_dedupes(
        self, restaurant, user, manager_staff, fake_http, django_capture_on_commit_callbacks
    ):
        services.register_device(user, restaurant, token="ExponentPushToken[abc]", platform="ios")
        services.register_device(manager_staff.user, restaurant, token="ExponentPushToken[dead]", platform="android")
        fake_http.queue.append(
            (200, {"data": [{"status": "ok"}, {"status": "error", "details": {"error": "DeviceNotRegistered"}}]})
        )
        with django_capture_on_commit_callbacks(execute=True):
            rows = services.notify(
                restaurant,
                "order.new",
                title="Order #1",
                body="table 4",
                data={"kind": "order", "id": "x"},
                dedupe_key="o:1",
            )
        assert len(rows) == 2
        call = fake_http.calls[-1]
        assert call["url"] == providers.EXPO_PUSH_URL and len(call["json"]) == 2
        by_token = {m["to"]: m for m in call["json"]}
        mine_msg = by_token["ExponentPushToken[abc]"]
        assert (
            mine_msg["data"]["kind"] == "order" and mine_msg["channelId"] == "orders" and mine_msg["sound"] == "default"
        )
        # the fake answered ok for the first message and DeviceNotRegistered for the second, whichever device that was
        errored = Notification.objects.filter(push_error="DeviceNotRegistered")
        assert errored.count() == 1 and Notification.objects.filter(push_error="", pushed_at__isnull=False).count() == 1
        dead_token = call["json"][1]["to"]
        assert Device.objects.get(token=dead_token).is_active is False
        assert Notification.objects.get(user=user).read_at is None
        # same dedupe key within the hour -> nothing new
        with django_capture_on_commit_callbacks(execute=True):
            assert services.notify(restaurant, "order.new", title="again", dedupe_key="o:1") == []
        assert Notification.objects.count() == 2

    def test_prefs_quiet_hours_and_email(
        self, restaurant, user, fake_http, settings, django_capture_on_commit_callbacks
    ):
        services.register_device(user, restaurant, token="ExponentPushToken[q]")
        now = timezone.localtime()
        StaffNotificationPrefs.objects.create(
            restaurant=restaurant,
            user=user,
            push=True,
            email=True,
            quiet_from=(now - timedelta(hours=1)).time(),
            quiet_to=(now + timedelta(hours=1)).time(),
        )
        from django.core import mail

        with django_capture_on_commit_callbacks(execute=True):
            services.notify(restaurant, "stock.low", title="Low: flour")
        assert fake_http.calls == []  # quiet hours: no push
        n = Notification.objects.get()
        assert n.push_error == "push off" and n.emailed_at is not None
        assert len(mail.outbox) == 1 and "Low: flour" in mail.outbox[0].subject

    def test_module_off_and_no_device(self, restaurant, user, fake_http, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            rows = services.notify(restaurant, "order.new", title="x")
        assert len(rows) == 1 and fake_http.calls == []
        rows[0].refresh_from_db()
        assert rows[0].push_error == "no device" and rows[0].pushed_at
        restaurant.notifications_enabled = False
        restaurant.save(update_fields=["notifications_enabled"])
        assert services.notify(restaurant, "order.new", title="y") == []

    def test_expo_outage_is_recorded(self, restaurant, user, fake_http, django_capture_on_commit_callbacks):
        services.register_device(user, restaurant, token="ExponentPushToken[z]")
        fake_http.queue.append((503, {"errors": ["down"]}))
        with django_capture_on_commit_callbacks(execute=True):
            services.notify(restaurant, "order.new", title="x")
        assert "503" in Notification.objects.get().push_error


@pytest.mark.django_db
class TestGuestMessages:
    def test_skipped_without_provider(self, restaurant, django_capture_on_commit_callbacks):
        cfg = services.settings_for(restaurant)
        cfg.guest_sms = True
        cfg.save()
        with django_capture_on_commit_callbacks(execute=True):
            msg = services.send_message(restaurant, "sms", "555 123 456", "hi", kind="test")
        msg.refresh_from_db()
        assert msg.status == "skipped" and "not configured" in msg.error and msg.to == "+995555123456"
        with django_capture_on_commit_callbacks(execute=True):
            msg = services.send_message(restaurant, "email", "a@b.ge", "hi", subject="s", kind="test")
        msg.refresh_from_db()
        assert msg.status == "sent" and msg.provider == "email"  # locmem backend counts as configured
        # switched off and not forced -> skipped before queuing
        cfg.guest_email = False
        cfg.save()
        msg = services.send_message(restaurant, "email", "a@b.ge", "hi", kind="test")
        assert msg.status == "skipped" and "switched off" in msg.error

    def test_http_gateway(self, restaurant, settings, fake_http, django_capture_on_commit_callbacks):
        settings.SMS_PROVIDER = "http"
        settings.SMS_HTTP_URL = "https://sms.example/send?to={to}&text={text}&from={from}"
        cfg = services.settings_for(restaurant)
        cfg.guest_sms = True
        cfg.sender_name = "Tiali"
        cfg.save()
        fake_http.queue.append((200, {"message_id": "m-1"}))
        with django_capture_on_commit_callbacks(execute=True):
            msg = services.send_message(restaurant, "sms", "+995555000111", "გამარჯობა", kind="test")
        msg.refresh_from_db()
        assert msg.status == "sent" and msg.provider_id == "m-1" and msg.provider == "sms:http"
        assert fake_http.calls[-1]["url"].startswith("https://sms.example/send?to=%2B995555000111&text=")
        assert "from=Tiali" in fake_http.calls[-1]["url"]
        fake_http.queue.append((500, {}))
        with django_capture_on_commit_callbacks(execute=True):
            msg2 = services.send_message(restaurant, "sms", "+995555000111", "x", kind="test")
        msg2.refresh_from_db()
        assert msg2.status == "failed" and "500" in msg2.error

    def test_twilio_and_console(self, restaurant, settings, fake_http):
        settings.SMS_PROVIDER = "twilio"
        settings.TWILIO_ACCOUNT_SID = "AC1"
        settings.TWILIO_AUTH_TOKEN = "tok"
        settings.TWILIO_FROM = "+10000"
        fake_http.queue.append((201, {"sid": "SM1"}))
        r = providers.send_sms("+995555000111", "hi")
        assert r.ok and r.provider_id == "SM1" and fake_http.calls[-1]["data"]["To"] == "+995555000111"
        settings.SMS_PROVIDER = "console"
        assert providers.send_sms("555000111", "hi").ok
        settings.SMS_PROVIDER = "none"
        assert providers.send_sms("555000111", "hi").skipped
        assert providers.normalize_phone("00995 555 000 111") == "+995555000111"


@pytest.mark.django_db
class TestHooksAndReminders:
    def test_reservation_lifecycle(
        self, restaurant, user, create_reservation, reservation_settings, settings, django_capture_on_commit_callbacks
    ):
        settings.SMS_PROVIDER = "console"
        cfg = services.settings_for(restaurant)
        cfg.guest_sms = True
        cfg.save()
        with django_capture_on_commit_callbacks(execute=True):
            r = create_reservation(restaurant=restaurant, status="pending", guest_phone="+995555000222")
            hooks.on_reservation_created(r)
        n = Notification.objects.get(user=user)
        assert n.event == "reservation.new" and "Test Guest" in n.body
        with django_capture_on_commit_callbacks(execute=True):
            r.confirm()
        msg = OutboundMessage.objects.get(kind="reservation_confirmation")
        assert msg.status == "sent" and r.confirmation_code in msg.body and "+995555000222" == msg.to
        with django_capture_on_commit_callbacks(execute=True):
            r.cancel(reason="no longer needed")
        assert Notification.objects.filter(event="reservation.cancelled").exists()

    def test_reminders(
        self, restaurant, create_reservation, reservation_settings, settings, django_capture_on_commit_callbacks
    ):
        settings.SMS_PROVIDER = "console"
        cfg = services.settings_for(restaurant)
        cfg.guest_sms = True
        cfg.save()
        reservation_settings.send_reminder = True
        reservation_settings.reminder_hours_before = 24
        reservation_settings.save()
        now = timezone.now()
        soon = timezone.localtime(now + timedelta(hours=3))
        late = timezone.localtime(now + timedelta(hours=60))
        r1 = create_reservation(
            restaurant=restaurant, reservation_date=soon.date(), reservation_time=soon.time().replace(microsecond=0)
        )
        r2 = create_reservation(
            restaurant=restaurant, reservation_date=late.date(), reservation_time=late.time().replace(microsecond=0)
        )
        with django_capture_on_commit_callbacks(execute=True):
            assert services.send_due_reminders(now) == 1
        r1.refresh_from_db()
        r2.refresh_from_db()
        assert r1.reminder_sent and not r2.reminder_sent
        assert OutboundMessage.objects.filter(kind="reservation_reminder", status="sent").count() == 1
        assert services.send_due_reminders(now) == 0  # not twice

    def test_order_and_inventory_hooks(self, restaurant, user, menu_item, django_capture_on_commit_callbacks):
        from apps.inventory import services as inventory
        from apps.orders.models import Order
        from apps.orders.services import transition_order

        order = Order.objects.create(
            restaurant=restaurant, order_type="dine_in", status="pending", source="qr", total=12
        )
        with django_capture_on_commit_callbacks(execute=True):
            hooks.on_order_created(order)
            hooks.on_order_created(
                Order.objects.create(restaurant=restaurant, order_type="dine_in", status="pending", source="pos")
            )
        assert Notification.objects.filter(event="order.new").count() == 1  # POS orders are silent
        with django_capture_on_commit_callbacks(execute=True):
            transition_order(order, "cancelled", cancellation_reason="Guest left")
        assert Notification.objects.filter(event="order.cancelled", user=user).exists()
        with django_capture_on_commit_callbacks(execute=True):
            inventory.open_alert(restaurant.pk, "out_of_stock", "ხინკალი sold out", menu_item=menu_item)
        n = Notification.objects.get(event="stock.out")
        assert n.title == "ხინკალი sold out" and n.dedupe_key.startswith("stock.out:")
        # hooks never raise
        hooks.on_review_created(None)


@pytest.mark.django_db
class TestApiAndAdmin:
    def test_dashboard_api(
        self, authenticated_owner_client, restaurant, user, owner_member, fake_http, django_capture_on_commit_callbacks
    ):
        api = authenticated_owner_client
        api.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        base = "/api/v1/dashboard/notifications/"
        res = api.post(
            f"{base}devices/",
            {"token": "ExponentPushToken[api]", "platform": "android", "app_version": "1.0"},
            format="json",
        )
        assert res.status_code == 201 and res.json()["platform"] == "android"
        fake_http.queue.append(EXPO_OK)
        with django_capture_on_commit_callbacks(execute=True):
            res = api.post(f"{base}test/")
        assert res.status_code == 200 and res.json()[0]["title"] == "Test notification"
        assert fake_http.calls[-1]["json"][0]["to"] == "ExponentPushToken[api]"
        res = api.get(f"{base}unread-count/")
        assert res.json()["unread"] == 1 and res.json()["latest_id"]
        res = api.get(f"{base}?unread=1")
        assert res.status_code == 200 and res.json()["count"] == 1
        nid = res.json()["results"][0]["id"]
        res = api.post(f"{base}read/", {"ids": [nid]}, format="json")
        assert res.json()["unread"] == 0
        res = api.put(
            f"{base}prefs/",
            {"muted_events": ["review.new"], "push": False, "quiet_from": "23:00", "quiet_to": "08:00"},
            format="json",
        )
        assert res.status_code == 200, res.content
        assert res.json()["push"] is False and any(
            e["muted"] for e in res.json()["events"] if e["code"] == "review.new"
        )
        assert api.put(f"{base}prefs/", {"muted_events": ["nope"]}, format="json").status_code == 400
        res = api.post(f"{base}test-message/", {"channel": "email", "to": "owner@example.com"}, format="json")
        assert res.status_code == 200 and res.json()["status"] in ("sent", "queued")
        res = api.delete(f"{base}devices/", {"token": "ExponentPushToken[api]"}, format="json")
        assert res.json()["removed"] == 1
        restaurant.notifications_enabled = False
        restaurant.save(update_fields=["notifications_enabled"])
        assert api.get(f"{base}unread-count/").status_code in (403, 404)

    def test_admin_page(self, user, restaurant, owner_member, manager_staff, settings):
        c = Client(HTTP_HOST=f"{restaurant.slug}.localhost")
        c.force_login(user)
        base = "/tenant-admin/notifications/notificationsettingspage/"
        page = c.get(base)
        assert page.status_code == 200
        html = page.content.decode()
        assert 'data-testid="notification-settings"' in html and "Manager Staff" in html
        res = c.post(
            f"{base}save/", {"guest_sms": "1", "sender_name": "Tiali", "reservation_reminder_ka": "შეხსენება {code}"}
        )
        assert res.status_code == 302
        cfg = services.settings_for(restaurant)
        assert cfg.guest_sms and cfg.sender_name == "Tiali" and cfg.reservation_reminder_ka == "შეხსენება {code}"
        assert cfg.reservation_reminder_en.startswith("{restaurant}: reminder")  # blank -> default
        res = c.post(f"{base}test/", {"channel": "email", "to": "x@y.ge"})
        assert res.status_code == 302 and OutboundMessage.objects.filter(kind="test").exists()
        assert c.get("/tenant-admin/notifications/outboundmessage/").status_code == 200
        # a waiter cannot open the settings page
        c2 = Client(HTTP_HOST=f"{restaurant.slug}.localhost")
        from apps.staff.models import StaffMember

        w = StaffMember.objects.filter(restaurant=restaurant, role__name="waiter").first()
        if w:
            c2.force_login(w.user)
            assert c2.get(base).status_code == 403
