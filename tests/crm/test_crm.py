"""Customers from orders / reservations / reviews, consent, segments, campaigns, automations, unsubscribe, admin, API."""

from datetime import timedelta
from decimal import Decimal

from django.test import Client
from django.utils import timezone

from rest_framework.test import APIClient

import pytest

from apps.crm import services
from apps.crm.models import Automation, Campaign, CampaignDelivery, Customer, Segment
from apps.notifications.models import OutboundMessage
from apps.orders.models import Order


@pytest.fixture
def crm(restaurant, settings):
    restaurant.crm_enabled = True
    restaurant.accepts_remote_orders = True
    restaurant.accepts_takeaway = True
    restaurant.cash_enabled = False
    restaurant.save(update_fields=["crm_enabled", "accepts_remote_orders", "accepts_takeaway", "cash_enabled"])
    settings.SMS_PROVIDER = "console"
    services.seed_segments(restaurant)
    return restaurant


def completed_order(restaurant, *, phone="", user=None, total="20", days_ago=0, email=""):
    o = Order.objects.create(
        restaurant=restaurant,
        order_type="takeaway",
        status="completed",
        source="web",
        customer=user,
        customer_phone=phone,
        customer_email=email,
        customer_name="Nino",
        total=Decimal(total),
        subtotal=Decimal(total),
        completed_at=timezone.now() - timedelta(days=days_ago),
    )
    return o


@pytest.mark.django_db
class TestCustomers:
    def test_identity_and_stats(self, crm, user, create_reservation):
        c1 = services.identify(crm, phone="555 000 111", name="Nino", source="qr")
        assert c1.phone == "+995555000111" and c1.name == "Nino"
        assert services.identify(crm, phone="+995555000111") == c1
        # a user with the same phone merges into that record
        user.phone_number = "+995555000111"
        user.save()
        assert services.identify(crm, user=user) == c1
        c1.refresh_from_db()
        assert c1.user == user
        completed_order(crm, phone="+995555000111", total="20")
        completed_order(crm, user=user, total="30", days_ago=2)
        r = create_reservation(restaurant=crm, guest_phone="+995555000111")
        r.mark_completed()
        services.rebuild(c1)
        assert c1.orders_count == 2 and c1.reservations_count == 1 and c1.visits == 3
        assert c1.total_spend == Decimal("50.00") and c1.avg_ticket == Decimal("25.00") and c1.last_visit_at
        assert c1.days_since_visit == 0

    def test_hooks_and_consent(self, crm, user, django_capture_on_commit_callbacks):
        from apps.orders.services import transition_order

        o = Order.objects.create(
            restaurant=crm,
            order_type="takeaway",
            status="pending",
            source="web",
            customer_phone="+995555000222",
            customer_name="Gio",
        )
        from apps.crm import hooks

        hooks.on_order_created(o, consent=True)
        c = Customer.objects.get(restaurant=crm, phone="+995555000222")
        assert c.marketing_opt_in and c.opt_in_source == "checkout" and c.visits == 0
        with django_capture_on_commit_callbacks(execute=True):
            transition_order(o, "confirmed")
            transition_order(o, "completed")
        c.refresh_from_db()
        assert c.orders_count == 1 and c.visits == 1
        services.set_consent(c, False, source="link")
        assert not c.marketing_opt_in and c.opt_out_at
        # profile consent propagates to every restaurant record of that user
        user.profile.marketing_opt_in = True
        user.profile.date_of_birth = timezone.localdate().replace(year=1990)
        user.profile.save()
        cu = services.identify(crm, user=user)
        assert cu.marketing_opt_in and cu.birthday
        user.profile.marketing_opt_in = False
        user.profile.save()
        services.sync_user_consent(user)
        cu.refresh_from_db()
        assert not cu.marketing_opt_in

    def test_backfill_and_segments(self, crm, user):
        completed_order(crm, phone="+995555000301", total="10")
        for i in range(3):
            completed_order(crm, phone="+995555000302", total="40", days_ago=i)
        completed_order(crm, phone="+995555000303", total="15", days_ago=60)
        assert services.backfill(crm) == 3
        for c in Customer.objects.filter(restaurant=crm):
            services.set_consent(c, True, source="test")
        seg = {s.name: s for s in Segment.objects.filter(restaurant=crm)}
        assert services.segment_count(seg["Everyone (opted in)"]) == 3
        assert services.segment_count(seg["Regulars"]) == 1
        assert services.segment_count(seg["Lapsed"]) == 1
        assert services.segment_count(seg["New guests"]) == 1
        Customer.objects.filter(phone="+995555000302").update(birthday=timezone.localdate())
        assert services.segment_count(seg["Birthday this month"]) == 1
        custom = Segment.objects.create(
            restaurant=crm, name="Big spenders", rules={"min_spend": 100, "tags_any": ["vip"]}
        )
        assert services.segment_count(custom) == 0
        Customer.objects.filter(phone="+995555000302").update(tags=["vip"])
        assert services.segment_count(custom) == 1


@pytest.mark.django_db
class TestCampaignsAndAutomations:
    def test_campaign_flow(self, crm, user, django_capture_on_commit_callbacks):
        from apps.promotions.models import Promotion

        for i in range(3):
            c = services.identify(crm, phone=f"+99555500040{i}", name=f"Guest {i}", email=f"g{i}@example.com")
            services.set_consent(c, i < 2, source="test")  # the third one did not opt in
        promo = Promotion.objects.create(
            restaurant=crm, name="Comeback", kind="promo_code", mode="percent", value=10, code="BACK10"
        )
        seg = Segment.objects.get(restaurant=crm, name="Everyone (opted in)")
        camp = Campaign.objects.create(
            restaurant=crm,
            name="Weekend",
            channel="sms",
            segment=seg,
            body="Hi {name}, use {code} at {restaurant}. Stop: {unsubscribe}",
            promotion=promo,
        )
        assert services.audience(camp).count() == 2
        text = services.preview(camp)
        assert "BACK10" in text and "/api/v1/crm/unsubscribe/" in text and crm.name in text
        with django_capture_on_commit_callbacks(execute=True):
            msg = services.send_test(camp, "+995555000999", by=user)
        msg.refresh_from_db()
        assert msg.status == "sent" and msg.kind == "campaign"
        with django_capture_on_commit_callbacks(execute=True):
            services.start_campaign(camp, by=user)
        camp.refresh_from_db()
        assert camp.status == "sent" and camp.audience_count == 2 and camp.sent_count == 2 and camp.skipped_count == 0
        assert CampaignDelivery.objects.filter(campaign=camp).count() == 2
        assert OutboundMessage.objects.filter(kind="campaign", status="sent").count() == 3
        with pytest.raises(services.CrmError):
            services.start_campaign(camp)
        # scheduling
        later = Campaign.objects.create(restaurant=crm, name="Later", channel="email", segment=seg, body="Hello {name}")
        services.start_campaign(later, when=timezone.now() + timedelta(hours=2))
        assert later.status == "scheduled" and services.run_scheduled() == 0
        later.scheduled_at = timezone.now() - timedelta(minutes=1)
        later.save()
        with django_capture_on_commit_callbacks(execute=True):
            assert services.run_scheduled() == 1
        later.refresh_from_db()
        assert later.status == "sent" and later.sent_count == 2
        assert OutboundMessage.objects.filter(kind="campaign", channel="email").count() == 2
        assert "unsubscribe" in OutboundMessage.objects.filter(kind="campaign", channel="email").first().body

    def test_unsubscribe_link(self, crm):
        c = services.identify(crm, phone="+995555000501")
        services.set_consent(c, True, source="test")
        token = services.unsubscribe_token(c)
        api = APIClient()
        res = api.get(f"/api/v1/crm/unsubscribe/{token}/")
        assert res.status_code == 200 and b"Unsubscribe" in res.content
        c.refresh_from_db()
        assert c.marketing_opt_in
        res = api.post(f"/api/v1/crm/unsubscribe/{token}/")
        assert res.status_code == 200
        c.refresh_from_db()
        assert not c.marketing_opt_in and c.opt_in_source == "test" and c.opt_out_at
        assert api.get("/api/v1/crm/unsubscribe/nope/").status_code == 404

    def test_automations(self, crm, user, django_capture_on_commit_callbacks):
        birthday = Automation.objects.get(restaurant=crm, kind="birthday")
        birthday.enabled = True
        birthday.save()
        c = services.identify(crm, phone="+995555000601", name="Ana Kh")
        services.set_consent(c, True, source="test")
        c.birthday = timezone.localdate().replace(year=1995)
        c.save()
        with django_capture_on_commit_callbacks(execute=True):
            assert services.run_birthdays() == 1
            assert services.run_birthdays() == 0  # once per year
        msg = OutboundMessage.objects.get(kind="automation")
        assert "Ana" in msg.body and msg.status == "sent"
        review = Automation.objects.get(restaurant=crm, kind="review_prompt")
        review.enabled = True
        review.delay_hours = 1
        review.save()
        o = completed_order(crm, phone="+995555000601", total="20")
        o.completed_at = timezone.now() - timedelta(hours=2)
        o.save()
        with django_capture_on_commit_callbacks(execute=True):
            assert services.run_review_prompts() == 1
            assert services.run_review_prompts() == 0
        assert OutboundMessage.objects.filter(kind="automation").count() == 2
        assert (
            "/profile/reviews" in OutboundMessage.objects.filter(kind="automation").order_by("-created_at").first().body
        )
        winback = Automation.objects.get(restaurant=crm, kind="winback")
        winback.enabled = True
        winback.lapsed_days = 30
        winback.save()
        c.last_visit_at = timezone.now() - timedelta(days=40)
        c.save()
        with django_capture_on_commit_callbacks(execute=True):
            assert services.run_winbacks() == 1
            assert services.run_winbacks() == 0
        # opted-out guests never hear from automations
        services.set_consent(c, False)
        c.birthday = timezone.localdate()
        c.save()
        assert services.run_birthdays() == 0


@pytest.mark.django_db
class TestApiAndAdmin:
    def test_api(self, authenticated_owner_client, crm, user, staff_roles, create_staff_member):
        create_staff_member(user=user, restaurant=crm, role=next(r for r in staff_roles if r.name == "owner"))
        api = authenticated_owner_client
        api.defaults["HTTP_X_RESTAURANT"] = crm.slug
        c = services.identify(crm, phone="+995555000701", name="Lasha")
        res = api.get("/api/v1/dashboard/crm/customers/lookup/?phone=555000701")
        assert res.status_code == 200 and res.json()["name"] == "Lasha"
        assert api.get("/api/v1/dashboard/crm/customers/lookup/?phone=555000000").status_code == 404
        res = api.post(f"/api/v1/dashboard/crm/customers/{c.pk}/consent/", {"marketing_opt_in": True}, format="json")
        assert res.status_code == 200 and res.json()["marketing_opt_in"] is True
        res = api.patch(
            f"/api/v1/dashboard/crm/customers/{c.pk}/", {"tags": ["vip"], "notes": "likes window"}, format="json"
        )
        assert res.status_code == 200 and res.json()["tags"] == ["vip"]
        res = api.get("/api/v1/dashboard/crm/customers/?q=lasha")
        assert res.json()["count"] == 1
        res = api.get("/api/v1/dashboard/crm/segments/")
        assert res.status_code == 200 and any(s["name"] == "Regulars" for s in res.json())
        assert api.get("/api/v1/dashboard/crm/summary/").json()["opted_in"] == 1
        assert api.get("/api/v1/dashboard/crm/campaigns/").status_code == 200
        # consent at checkout and booking
        anon = APIClient()
        from apps.menu.models import MenuItem

        item = MenuItem.objects.filter(restaurant=crm).first()
        if item is not None:
            res = anon.post(
                "/api/v1/orders/create/",
                {
                    "restaurant_slug": crm.slug,
                    "order_type": "takeaway",
                    "customer_phone": "+995555000702",
                    "customer_name": "Keti",
                    "marketing_opt_in": True,
                    "items": [{"menu_item_id": str(item.pk), "quantity": 1}],
                },
                format="json",
            )
            assert res.status_code == 201, res.content
            assert Customer.objects.get(restaurant=crm, phone="+995555000702").marketing_opt_in
        res = anon.post(
            "/api/v1/reservations/create/",
            {
                "restaurant_slug": crm.slug,
                "guest_name": "Tako",
                "guest_phone": "+995555000703",
                "reservation_date": str(timezone.localdate() + timedelta(days=2)),
                "reservation_time": "19:00",
                "party_size": 2,
                "marketing_opt_in": True,
            },
            format="json",
            HTTP_X_RESTAURANT=crm.slug,
        )
        assert res.status_code in (200, 201), res.content
        assert Customer.objects.get(restaurant=crm, phone="+995555000703").marketing_opt_in
        # profile consent through /users/me/
        res = api.patch(
            "/api/v1/users/me/", {"profile": {"marketing_opt_in": True, "date_of_birth": "1990-05-05"}}, format="json"
        )
        assert res.status_code == 200, res.content
        user.profile.refresh_from_db()
        assert user.profile.marketing_opt_in and user.profile.marketing_opt_in_at

    def test_admin(self, crm, user, staff_roles, create_staff_member, settings):
        create_staff_member(user=user, restaurant=crm, role=next(r for r in staff_roles if r.name == "owner"))
        c = services.identify(crm, phone="+995555000801", name="Mari")
        services.set_consent(c, True, source="test")
        client = Client(HTTP_HOST=f"{crm.slug}.localhost")
        client.force_login(user)
        assert client.get("/tenant-admin/crm/customer/").status_code == 200
        page = client.get(f"/tenant-admin/crm/customer/{c.pk}/change/")
        assert page.status_code == 200 and 'data-testid="customer-timeline"' in page.content.decode()
        assert client.get("/tenant-admin/crm/segment/").status_code == 200
        res = client.post(
            "/tenant-admin/crm/segment/add/",
            {"name": "VIPs", "description": "", "is_active": "on", "rules_text": '{"tags_any": ["vip"]}'},
        )
        assert res.status_code == 302, res.content[:300]
        assert Segment.objects.get(restaurant=crm, name="VIPs").rules == {"tags_any": ["vip"]}
        res = client.post(
            "/tenant-admin/crm/segment/add/", {"name": "Bad", "rules_text": "{not json", "is_active": "on"}
        )
        assert res.status_code == 200 and "valid JSON" in res.content.decode()
        seg = Segment.objects.get(restaurant=crm, name="Everyone (opted in)")
        res = client.post(
            "/tenant-admin/crm/campaign/add/",
            {
                "name": "Hello",
                "channel": "sms",
                "segment": str(seg.pk),
                "subject": "",
                "body": "Hi {name}",
                "scheduled_at": "",
            },
        )
        assert res.status_code == 302, res.content[:500]
        camp = Campaign.objects.get(restaurant=crm, name="Hello")
        page = client.get(f"/tenant-admin/crm/campaign/{camp.pk}/change/")
        assert (
            page.status_code == 200
            and 'data-testid="campaign-panel"' in page.content.decode()
            and "Hi Mari" in page.content.decode()
        )
        assert client.post(f"/tenant-admin/crm/campaign/{camp.pk}/test/", {"to": "+995555000999"}).status_code == 302
        assert client.get(f"/tenant-admin/crm/campaign/{camp.pk}/send-now/").status_code == 302
        camp.refresh_from_db()
        assert camp.status in ("sending", "sent")
        assert client.get("/tenant-admin/crm/automation/").status_code == 200
        assert Automation.objects.filter(restaurant=crm).count() == 3
        assert client.get("/tenant-admin/reports/crmreport/").status_code == 200
        crm.crm_enabled = False
        crm.save(update_fields=["crm_enabled"])
        assert client.get("/tenant-admin/crm/customer/").status_code == 403
