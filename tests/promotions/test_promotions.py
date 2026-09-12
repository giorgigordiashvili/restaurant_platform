"""Schedules, '86 today', happy hours, promo codes, combos, dashboard / public APIs and the tenant admin."""

from datetime import timedelta
from decimal import Decimal

from django.test import Client
from django.utils import timezone

from rest_framework.test import APIClient

import pytest

from apps.orders.models import Order
from apps.promotions import services
from apps.promotions.availability import availability, local_now
from apps.promotions.models import MenuSchedule, Promotion, PromotionUse


@pytest.fixture
def promo_restaurant(restaurant):
    restaurant.promotions_enabled = True
    restaurant.accepts_remote_orders = True
    restaurant.accepts_takeaway = True
    restaurant.cash_enabled = False
    restaurant.save(update_fields=["promotions_enabled", "accepts_remote_orders", "accepts_takeaway", "cash_enabled"])
    return restaurant


def window(restaurant, *, name="Now", hours_before=1, hours_after=1, weekdays=None):
    now = local_now(restaurant)
    start = (now - timedelta(hours=hours_before)).time().replace(second=0, microsecond=0)
    end = (now + timedelta(hours=hours_after)).time().replace(second=0, microsecond=0)
    return MenuSchedule.objects.create(
        restaurant=restaurant, name=name, weekdays=weekdays or [], start_time=start, end_time=end
    )


def closed_window(restaurant, name="Later"):
    now = local_now(restaurant)
    start = (now + timedelta(hours=2)).time().replace(second=0, microsecond=0)
    end = (now + timedelta(hours=3)).time().replace(second=0, microsecond=0)
    return MenuSchedule.objects.create(restaurant=restaurant, name=name, weekdays=[], start_time=start, end_time=end)


@pytest.fixture
def owner_api(authenticated_owner_client, promo_restaurant, user, staff_roles, create_staff_member):
    create_staff_member(user=user, restaurant=promo_restaurant, role=next(r for r in staff_roles if r.name == "owner"))
    authenticated_owner_client.defaults["HTTP_X_RESTAURANT"] = promo_restaurant.slug
    return authenticated_owner_client


def pos_order(api, menu_item, qty=2, **extra):
    body = {"order_type": "takeaway", "items": [{"menu_item_id": str(menu_item.pk), "quantity": qty}], **extra}
    res = api.post("/api/v1/dashboard/orders/create/", body, format="json")
    assert res.status_code in (200, 201), res.content
    data = res.json()
    return Order.objects.get(pk=(data.get("data") or data).get("id"))


@pytest.mark.django_db
class TestSchedulesAndAvailability:
    def test_schedule_matching(self, promo_restaurant):
        s = window(promo_restaurant)
        now = local_now(promo_restaurant)
        assert s.matches(now)
        s.weekdays = [(now.weekday() + 1) % 7]
        assert not s.matches(now)
        overnight = MenuSchedule.objects.create(
            restaurant=promo_restaurant,
            name="Night",
            start_time=timezone.datetime(2000, 1, 1, 22, 0).time(),
            end_time=timezone.datetime(2000, 1, 1, 3, 0).time(),
        )
        late = now.replace(hour=23, minute=30)
        early = now.replace(hour=1, minute=0)
        noon = now.replace(hour=12, minute=0)
        assert overnight.matches(late) and overnight.matches(early) and not overnight.matches(noon)
        assert "22:00–03:00" in overnight.label()

    def test_item_and_category_windows_and_86(self, promo_restaurant, menu_item, menu_category, api_client):
        assert availability(menu_item) == (True, "")
        menu_item.schedule = closed_window(promo_restaurant)
        menu_item.save()
        ok, reason = availability(menu_item)
        assert not ok and "–" in reason
        menu_item.schedule = None
        menu_item.save()
        menu_category.schedule = closed_window(promo_restaurant, "Lunch")
        menu_category.save()
        menu_item.refresh_from_db()
        assert availability(menu_item)[0] is False
        menu_category.schedule = window(promo_restaurant)
        menu_category.save()
        menu_item.refresh_from_db()
        assert availability(menu_item)[0] is True
        services.set_unavailable(menu_item, "today")
        assert menu_item.unavailable_until > timezone.now()
        assert availability(menu_item) == (False, "Not available today")
        # public menu still lists it but flags it; ordering refuses it
        res = api_client.get(f"/api/v1/menu/{promo_restaurant.slug}/")
        row = next(
            i for c in res.json()["data"]["menu"]["categories"] for i in c["items"] if i["id"] == str(menu_item.pk)
        )
        assert row["available_now"] is False and row["available_from"] == "Not available today"
        res = api_client.post(
            "/api/v1/orders/create/",
            {
                "restaurant_slug": promo_restaurant.slug,
                "order_type": "takeaway",
                "items": [{"menu_item_id": str(menu_item.pk), "quantity": 1}],
            },
            format="json",
        )
        assert res.status_code == 400 and "Not available today" in res.content.decode()
        services.set_unavailable(menu_item, None)
        assert availability(menu_item)[0] is True

    def test_86_api(self, owner_api, menu_item, waiter_staff, promo_restaurant):
        res = owner_api.post(
            f"/api/v1/dashboard/promotions/items/{menu_item.pk}/86/", {"until": "tomorrow"}, format="json"
        )
        assert res.status_code == 200 and res.json()["available_now"] is False
        res = owner_api.get("/api/v1/dashboard/promotions/unavailable/")
        assert [r["id"] for r in res.json()] == [str(menu_item.pk)]
        res = owner_api.post(
            f"/api/v1/dashboard/promotions/items/{menu_item.pk}/86/", {"until": "clear"}, format="json"
        )
        assert res.json()["available_now"] is True and res.json()["unavailable_until"] is None
        from rest_framework_simplejwt.tokens import RefreshToken

        waiter = APIClient()
        waiter.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(waiter_staff.user).access_token}")
        waiter.defaults["HTTP_X_RESTAURANT"] = promo_restaurant.slug
        assert (
            waiter.post(
                f"/api/v1/dashboard/promotions/items/{menu_item.pk}/86/", {"until": "today"}, format="json"
            ).status_code
            == 403
        )


@pytest.mark.django_db
class TestHappyHour:
    def test_auto_apply_and_menu_price(
        self, owner_api, promo_restaurant, menu_item, menu_category, create_menu_item, api_client
    ):
        other = create_menu_item(
            restaurant=promo_restaurant, category=menu_category, name="Full price", price=Decimal("8")
        )
        promo = Promotion.objects.create(
            restaurant=promo_restaurant,
            name="Happy hour",
            kind="happy_hour",
            mode="percent",
            value=20,
            applies_to="items",
            schedule=window(promo_restaurant),
        )
        promo.items.add(menu_item)
        prices = services.promo_prices(promo_restaurant, [menu_item, other])
        assert prices[menu_item.pk][0] == Decimal("8.00") and other.pk not in prices
        res = api_client.get(f"/api/v1/menu/{promo_restaurant.slug}/")
        items = {i["id"]: i for c in res.json()["data"]["menu"]["categories"] for i in c["items"]}
        assert (
            items[str(menu_item.pk)]["promo_price"] == "8.00"
            and items[str(menu_item.pk)]["promo_label"] == "Happy hour"
        )
        assert items[str(other.pk)]["promo_price"] is None
        order = pos_order(owner_api, menu_item, qty=2)
        line = order.items.get()
        assert (
            line.discount_amount == Decimal("4.00")
            and line.promotion == promo
            and line.discount_reason_text == "Happy hour"
        )
        assert order.discount_amount == Decimal("4.00") and order.total == Decimal("16.00")
        promo.refresh_from_db()
        assert promo.uses_count == 1 and PromotionUse.objects.get(order=order).amount == Decimal("4.00")
        # outside the window: nothing applied
        promo.schedule = closed_window(promo_restaurant)
        promo.save()
        order2 = pos_order(owner_api, menu_item, qty=1)
        assert order2.items.get().discount_amount == 0 and order2.total == Decimal("10.00")
        # module off: nothing applied
        promo.schedule = window(promo_restaurant, name="Again")
        promo.save()
        promo_restaurant.promotions_enabled = False
        promo_restaurant.save(update_fields=["promotions_enabled"])
        assert pos_order(owner_api, menu_item, qty=1).discount_amount == 0

    def test_manual_discount_wins(self, owner_api, promo_restaurant, menu_item, user):
        from apps.orders import services as order_services

        promo = Promotion.objects.create(
            restaurant=promo_restaurant,
            name="HH",
            kind="happy_hour",
            mode="fixed",
            value=Decimal("1"),
            applies_to="all",
            schedule=window(promo_restaurant),
        )
        order = pos_order(owner_api, menu_item, qty=1)
        line = order.items.get()
        assert line.promotion == promo
        order_services.comp_item(line, by=user, reason_text="VIP")
        services.apply_automatic(order, channel="pos")
        line.refresh_from_db()
        assert line.is_comped and line.promotion == promo  # comps are left alone


@pytest.mark.django_db
class TestPromoCodes:
    def _code(self, restaurant, **kw):
        defaults = dict(
            restaurant=restaurant,
            name="Welcome",
            kind="promo_code",
            mode="percent",
            value=10,
            applies_to="all",
            code="welcome10",
        )
        defaults.update(kw)
        return Promotion.objects.create(**defaults)

    def test_redeem_at_creation_and_via_endpoint(self, owner_api, promo_restaurant, menu_item):
        promo = self._code(promo_restaurant)
        assert promo.code == "WELCOME10"
        order = pos_order(owner_api, menu_item, qty=2, promo_code="welcome10")
        d = order.discounts.get()
        assert (
            d.kind == "promo"
            and d.amount == Decimal("2.00")
            and d.promotion == promo
            and order.total == Decimal("18.00")
        )
        promo.refresh_from_db()
        assert promo.uses_count == 1
        # again via the order endpoint on a new order
        order2 = pos_order(owner_api, menu_item, qty=1)
        res = owner_api.post(
            f"/api/v1/dashboard/promotions/orders/{order2.pk}/promo-code/", {"code": "WELCOME10"}, format="json"
        )
        assert res.status_code == 200 and Decimal(res.json()["total"]) == Decimal("9.00")
        res = owner_api.post(
            f"/api/v1/dashboard/promotions/orders/{order2.pk}/promo-code/", {"code": "WELCOME10"}, format="json"
        )
        assert res.status_code == 400 and res.json()["error"]["code"] in ("not_stackable", "already_applied")
        res = owner_api.delete(f"/api/v1/dashboard/promotions/orders/{order2.pk}/promo-code/")
        assert res.status_code == 200 and Decimal(res.json()["total"]) == Decimal("10.00")
        promo.refresh_from_db()
        assert promo.uses_count == 1

    def test_validation_rules(self, owner_api, promo_restaurant, menu_item, api_client):
        promo = self._code(promo_restaurant, min_order_amount=Decimal("15"), max_uses=1, channels=["web", "pos"])
        with pytest.raises(services.PromotionError) as exc:
            services.check_code(promo_restaurant, "nope")
        assert exc.value.code == "invalid_code"
        order = pos_order(owner_api, menu_item, qty=1)  # 10 < 15
        with pytest.raises(services.PromotionError) as exc:
            services.redeem_code(order, "welcome10", channel="pos")
        assert exc.value.code == "min_order"
        with pytest.raises(services.PromotionError) as exc:
            services.redeem_code(order, "welcome10", channel="qr")
        assert exc.value.code == "wrong_channel"
        big = pos_order(owner_api, menu_item, qty=2)
        services.redeem_code(big, "welcome10", channel="pos")
        with pytest.raises(services.PromotionError) as exc:
            services.check_code(promo_restaurant, "welcome10")
        assert exc.value.code == "exhausted"
        promo.max_uses = 0
        promo.ends_on = timezone.localdate() - timedelta(days=1)
        promo.save()
        with pytest.raises(services.PromotionError) as exc:
            services.check_code(promo_restaurant, "welcome10")
        assert exc.value.code == "expired"
        promo.ends_on = None
        promo.max_uses_per_customer = 1
        promo.save()
        services.check_code(promo_restaurant, "welcome10", phone="")  # unknown guest: allowed

    def test_per_customer_limit_and_public_validate(self, owner_api, promo_restaurant, menu_item, api_client):
        promo = self._code(promo_restaurant, max_uses_per_customer=1)
        order = pos_order(owner_api, menu_item, qty=1, customer_phone="+995555000999")
        services.redeem_code(order, "welcome10", channel="pos")
        with pytest.raises(services.PromotionError) as exc:
            services.check_code(promo_restaurant, "welcome10", phone="+995555000999")
        assert exc.value.code == "already_used"
        api_client = APIClient()  # an anonymous guest (the owner already used the code above)
        res = api_client.post(
            f"/api/v1/promotions/{promo_restaurant.slug}/validate/",
            {"code": "welcome10", "items": [{"menu_item_id": str(menu_item.pk), "quantity": 3}]},
            format="json",
        )
        assert res.status_code == 200 and res.json()["valid"], res.json()
        assert Decimal(res.json()["discount"]) == Decimal("3.00")
        res = api_client.post(f"/api/v1/promotions/{promo_restaurant.slug}/validate/", {"code": "zzz"}, format="json")
        assert res.json()["valid"] is False and res.json()["error_code"] == "invalid_code"
        # customer order with a code
        res = api_client.post(
            "/api/v1/orders/create/",
            {
                "restaurant_slug": promo_restaurant.slug,
                "order_type": "takeaway",
                "promo_code": "welcome10",
                "items": [{"menu_item_id": str(menu_item.pk), "quantity": 2}],
            },
            format="json",
        )
        assert res.status_code == 201, res.content
        order = Order.objects.get(order_number=res.json()["data"]["order_number"])
        assert order.discount_amount == Decimal("2.00")
        res = api_client.post(
            "/api/v1/orders/create/",
            {
                "restaurant_slug": promo_restaurant.slug,
                "order_type": "takeaway",
                "promo_code": "bad",
                "items": [{"menu_item_id": str(menu_item.pk), "quantity": 1}],
            },
            format="json",
        )
        assert res.status_code == 400 and "promo_code" in res.content.decode()

    def test_not_stackable_with_happy_hour(self, owner_api, promo_restaurant, menu_item):
        Promotion.objects.create(
            restaurant=promo_restaurant,
            name="HH",
            kind="happy_hour",
            mode="percent",
            value=10,
            applies_to="all",
            schedule=window(promo_restaurant),
        )
        self._code(promo_restaurant)
        order = pos_order(owner_api, menu_item, qty=1)
        with pytest.raises(services.PromotionError) as exc:
            services.redeem_code(order, "welcome10", channel="pos")
        assert exc.value.code == "not_stackable"
        Promotion.objects.filter(code="WELCOME10").update(stackable=True)
        services.redeem_code(order, "welcome10", channel="pos")
        order.refresh_from_db()
        assert order.discount_amount == Decimal("1.90")  # 1.00 HH + 10% of 9.00


@pytest.mark.django_db
class TestCombos:
    def test_combo_consumes_components(self, promo_restaurant, menu_item, menu_category, create_menu_item):
        from apps.inventory.models import RecipeLine, StockItem, UnitOfMeasure
        from apps.inventory.services import needs_for
        from apps.promotions.models import ComboComponent

        g = UnitOfMeasure.objects.get(code="g")
        flour = StockItem.objects.create(restaurant=promo_restaurant, name="Flour", base_unit=g, purchase_unit=g)
        RecipeLine.objects.create(menu_item=menu_item, stock_item=flour, quantity=Decimal("100"), unit=g)
        combo = create_menu_item(
            restaurant=promo_restaurant, category=menu_category, name="Set", price=Decimal("25"), is_combo=True
        )
        ComboComponent.objects.create(combo=combo, item=menu_item, quantity=2)
        assert needs_for(combo, 3)[flour.pk] == Decimal("600")


@pytest.mark.django_db
class TestAdminAndDashboard:
    def test_pages(self, user, promo_restaurant, staff_roles, create_staff_member, menu_item, menu_category):
        create_staff_member(
            user=user, restaurant=promo_restaurant, role=next(r for r in staff_roles if r.name == "owner")
        )
        c = Client(HTTP_HOST=f"{promo_restaurant.slug}.localhost")
        c.force_login(user)
        assert c.get("/tenant-admin/promotions/promotion/").status_code == 200
        assert c.get("/tenant-admin/promotions/menuschedule/add/").status_code == 200
        res = c.post(
            "/tenant-admin/promotions/menuschedule/add/",
            {
                "name": "Breakfast",
                "weekdays": ["0", "1"],
                "start_time": "08:00",
                "end_time": "11:30",
                "is_active": "on",
            },
        )
        assert res.status_code == 302, res.content[:300]
        s = MenuSchedule.objects.get(name="Breakfast")
        assert s.restaurant == promo_restaurant and s.weekdays == [0, 1]
        res = c.post(
            "/tenant-admin/promotions/promotion/add/",
            {
                "name": "Lunch deal",
                "kind": "happy_hour",
                "is_active": "on",
                "mode": "percent",
                "value": "15",
                "applies_to": "categories",
                "categories": [str(menu_category.pk)],
                "schedule": str(s.pk),
                "channels": ["pos"],
                "min_order_amount": "0",
                "max_uses": "0",
                "max_uses_per_customer": "0",
                "code": "",
            },
        )
        assert res.status_code == 302, res.content[:500]
        p = Promotion.objects.get(name="Lunch deal")
        assert (
            p.restaurant == promo_restaurant and list(p.categories.all()) == [menu_category] and p.channels == ["pos"]
        )
        # a promo code without a code is refused
        res = c.post(
            "/tenant-admin/promotions/promotion/add/",
            {
                "name": "Bad",
                "kind": "promo_code",
                "mode": "fixed",
                "value": "5",
                "applies_to": "all",
                "min_order_amount": "0",
                "max_uses": "0",
                "max_uses_per_customer": "0",
            },
        )
        assert res.status_code == 200 and "needs a code" in res.content.decode()
        page = c.get(f"/tenant-admin/menu/menuitem/{menu_item.pk}/change/")
        assert (
            page.status_code == 200
            and "combo_components" in page.content.decode()
            and 'name="schedule"' in page.content.decode()
        )
        home = c.get("/tenant-admin/").content.decode()
        assert 'data-testid="card-promotions"' in home or "Happy hours live now" in home
        promo_restaurant.promotions_enabled = False
        promo_restaurant.save(update_fields=["promotions_enabled"])
        assert c.get("/tenant-admin/promotions/promotion/").status_code == 403
