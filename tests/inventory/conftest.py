"""
A restaurant with the warehouse switched on, seeded units, one stock item
("Flour", tracked in grams, bought in 10 kg bags) with two lots of different
expiry, a dish ("Pizza": 200 g flour) and a modifier ("Extra cheese": 50 g
cheese). Staff of every role, with API and tenant-admin clients.
"""

from decimal import Decimal

from django.test import Client

from rest_framework.test import APIClient

import pytest

from apps.inventory import services
from apps.inventory.models import RecipeLine, StockItem, UnitOfMeasure
from apps.menu.models import Modifier
from apps.orders.models import OrderItemModifier
from apps.staff.models import StaffRole


@pytest.fixture(autouse=True)
def _tenant_domain(settings):
    settings.MAIN_DOMAIN = "localhost"


@pytest.fixture
def wh(restaurant):
    restaurant.warehouse_enabled = True
    restaurant.save(update_fields=["warehouse_enabled"])
    return restaurant


@pytest.fixture
def units(db):
    return {u.code: u for u in UnitOfMeasure.objects.all()}


@pytest.fixture
def flour(wh, units):
    return StockItem.objects.create(
        restaurant=wh,
        name="Flour",
        base_unit=units["g"],
        purchase_unit=units["kg"],
        purchase_pack_qty=Decimal("10"),
        min_level=Decimal("300"),
        par_level=Decimal("2000"),
        expiry_warning_days=3,
    )


@pytest.fixture
def cheese(wh, units):
    return StockItem.objects.create(
        restaurant=wh, name="Cheese", base_unit=units["g"], min_level=Decimal("100"), par_level=Decimal("500")
    )


@pytest.fixture
def lots(flour, units):
    """Two flour lots: A (500 g, expires in 2 days, 2.00/kg) and B (1 kg, expires in 10 days, 3.00/kg)."""
    from django.utils import timezone

    today = timezone.localdate()
    a = services.receive_stock(
        flour, "0.5", units["kg"], unit_cost="2", expiry_date=today + timezone.timedelta(days=2), reference="A"
    )
    b = services.receive_stock(
        flour, "1", units["kg"], unit_cost="3", expiry_date=today + timezone.timedelta(days=10), reference="B"
    )
    flour.refresh_from_db()
    return a, b


@pytest.fixture
def pizza(wh, create_menu_item, menu_category, flour, units):
    item = create_menu_item(wh, category=menu_category, name="Pizza", price=Decimal("20.00"))
    RecipeLine.objects.create(menu_item=item, stock_item=flour, quantity=Decimal("200"), unit=units["g"])
    return item


@pytest.fixture
def salad(wh, create_menu_item, menu_category):
    """A dish without a recipe: never tracked."""
    return create_menu_item(wh, category=menu_category, name="Salad", price=Decimal("8.00"))


@pytest.fixture
def extra_cheese(wh, create_modifier_group, cheese, units):
    group = create_modifier_group(wh, name="Extras")
    mod = Modifier(group=group, price_adjustment=Decimal("2.00"))
    mod.set_current_language("en")
    mod.name = "Extra cheese"
    mod.save()
    RecipeLine.objects.create(modifier=mod, stock_item=cheese, quantity=Decimal("50"), unit=units["g"])
    return mod


@pytest.fixture
def roles(wh):
    return {r.name: r for r in StaffRole.create_default_roles(wh)}


def _staff(create_user, create_staff_member, restaurant, roles, role_name, email):
    user = create_user(email=email, first_name=role_name.title(), last_name="Staff")
    create_staff_member(user=user, restaurant=restaurant, role=roles[role_name])
    return user


@pytest.fixture
def manager(create_user, create_staff_member, wh, roles):
    return _staff(create_user, create_staff_member, wh, roles, "manager", "wh-manager@example.com")


@pytest.fixture
def warehouse_manager(create_user, create_staff_member, wh, roles):
    return _staff(create_user, create_staff_member, wh, roles, "warehouse_manager", "wh-whm@example.com")


@pytest.fixture
def kitchen(create_user, create_staff_member, wh, roles):
    return _staff(create_user, create_staff_member, wh, roles, "kitchen", "wh-kitchen@example.com")


@pytest.fixture
def waiter(create_user, create_staff_member, wh, roles):
    return _staff(create_user, create_staff_member, wh, roles, "waiter", "wh-waiter@example.com")


def api(user, restaurant):
    client = APIClient()
    client.force_authenticate(user)
    client.credentials(HTTP_X_RESTAURANT=restaurant.slug)
    return client


def admin(user, restaurant):
    client = Client(HTTP_HOST=f"{restaurant.slug}.localhost")
    client.force_login(user)
    return client


@pytest.fixture
def manager_api(manager, wh):
    return api(manager, wh)


@pytest.fixture
def manager_admin(manager, wh):
    return admin(manager, wh)


def make_order(create_order, create_order_item, restaurant, lines, **order_kwargs):
    """lines = [(menu_item, qty, [modifiers])]; builds an Order like the create views do."""
    order = create_order(restaurant=restaurant, **order_kwargs)
    for menu_item, qty, mods in lines:
        oi = create_order_item(order, menu_item=menu_item, item_name=str(menu_item), quantity=qty)
        for mod in mods:
            OrderItemModifier.objects.create(
                order_item=oi, modifier=mod, modifier_name=str(mod), price_adjustment=mod.price_adjustment
            )
        oi.recalculate_total()
    order.calculate_totals()
    return order


@pytest.fixture
def order_factory(create_order, create_order_item, wh):
    def _make(lines, **kwargs):
        return make_order(create_order, create_order_item, wh, lines, **kwargs)

    return _make
