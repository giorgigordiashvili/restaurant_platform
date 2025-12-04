"""
Pytest configuration and fixtures for the restaurant platform tests.
"""

from rest_framework.test import APIClient

import pytest
from rest_framework_simplejwt.tokens import RefreshToken


@pytest.fixture
def api_client():
    """Return an API client for testing."""
    return APIClient()


@pytest.fixture
def user_data():
    """Return basic user data for registration."""
    return {
        "email": "testuser@example.com",
        "password": "TestPassword123!",
        "password_confirm": "TestPassword123!",
        "first_name": "Test",
        "last_name": "User",
        "preferred_language": "en",
    }


@pytest.fixture
def create_user(db):
    """Factory fixture to create users."""
    from apps.accounts.models import User

    def _create_user(
        email="user@example.com", password="TestPassword123!", first_name="Test", last_name="User", **kwargs
    ):
        user = User.objects.create_user(
            email=email, password=password, first_name=first_name, last_name=last_name, **kwargs
        )
        return user

    return _create_user


@pytest.fixture
def user(create_user):
    """Create and return a test user."""
    return create_user()


@pytest.fixture
def another_user(create_user):
    """Create and return another test user."""
    return create_user(email="another@example.com", first_name="Another", last_name="User")


@pytest.fixture
def authenticated_client(api_client, user):
    """Return an authenticated API client."""
    refresh = RefreshToken.for_user(user)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return api_client


@pytest.fixture
def user_tokens(user):
    """Return access and refresh tokens for a user."""
    refresh = RefreshToken.for_user(user)
    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
    }


@pytest.fixture
def admin_user(create_user):
    """Create and return an admin user."""
    return create_user(
        email="admin@example.com", first_name="Admin", last_name="User", is_staff=True, is_superuser=True
    )


@pytest.fixture
def authenticated_admin_client(api_client, admin_user):
    """Return an authenticated API client with admin privileges."""
    refresh = RefreshToken.for_user(admin_user)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return api_client


# ============== Restaurant Fixtures ==============


@pytest.fixture
def create_restaurant(db):
    """Factory fixture to create restaurants."""
    from apps.tenants.models import Restaurant

    def _create_restaurant(owner, name="Test Restaurant", slug="test-restaurant", **kwargs):
        restaurant = Restaurant.objects.create(owner=owner, name=name, slug=slug, is_active=True, **kwargs)
        return restaurant

    return _create_restaurant


@pytest.fixture
def restaurant(create_restaurant, user):
    """Create and return a test restaurant owned by the test user."""
    return create_restaurant(owner=user)


@pytest.fixture
def another_restaurant(create_restaurant, another_user):
    """Create and return another test restaurant."""
    return create_restaurant(
        owner=another_user,
        name="Another Restaurant",
        slug="another-restaurant",
    )


@pytest.fixture
def restaurant_with_hours(restaurant):
    """Create restaurant with default operating hours."""
    from apps.tenants.models import RestaurantHours

    RestaurantHours.create_default_hours(restaurant)
    return restaurant


# ============== Staff Fixtures ==============


@pytest.fixture
def create_staff_role(db):
    """Factory fixture to create staff roles."""
    from apps.staff.models import StaffRole

    def _create_staff_role(restaurant, name="waiter", **kwargs):
        role = StaffRole.objects.create(restaurant=restaurant, name=name, **kwargs)
        return role

    return _create_staff_role


@pytest.fixture
def staff_roles(restaurant):
    """Create default staff roles for a restaurant."""
    from apps.staff.models import StaffRole

    return StaffRole.create_default_roles(restaurant)


@pytest.fixture
def create_staff_member(db):
    """Factory fixture to create staff members."""
    from apps.staff.models import StaffMember

    def _create_staff_member(user, restaurant, role, **kwargs):
        member = StaffMember.objects.create(user=user, restaurant=restaurant, role=role, **kwargs)
        return member

    return _create_staff_member


@pytest.fixture
def waiter_user(create_user):
    """Create a user for waiter role."""
    return create_user(email="waiter@example.com", first_name="Waiter", last_name="Staff")


@pytest.fixture
def waiter_staff(create_staff_member, waiter_user, restaurant, staff_roles):
    """Create a waiter staff member."""
    waiter_role = next(r for r in staff_roles if r.name == "waiter")
    return create_staff_member(
        user=waiter_user,
        restaurant=restaurant,
        role=waiter_role,
    )


@pytest.fixture
def manager_user(create_user):
    """Create a user for manager role."""
    return create_user(email="manager@example.com", first_name="Manager", last_name="Staff")


@pytest.fixture
def manager_staff(create_staff_member, manager_user, restaurant, staff_roles):
    """Create a manager staff member."""
    manager_role = next(r for r in staff_roles if r.name == "manager")
    return create_staff_member(
        user=manager_user,
        restaurant=restaurant,
        role=manager_role,
    )


@pytest.fixture
def authenticated_owner_client(api_client, user):
    """Return an authenticated client for restaurant owner."""
    refresh = RefreshToken.for_user(user)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return api_client


@pytest.fixture
def authenticated_manager_client(api_client, manager_user):
    """Return an authenticated client for manager."""
    refresh = RefreshToken.for_user(manager_user)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return api_client


@pytest.fixture
def authenticated_waiter_client(api_client, waiter_user):
    """Return an authenticated client for waiter."""
    refresh = RefreshToken.for_user(waiter_user)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return api_client


# ============== Menu Fixtures ==============


@pytest.fixture
def create_menu_category(db):
    """Factory fixture to create menu categories."""
    from apps.menu.models import MenuCategory

    def _create_category(restaurant, name="Test Category", **kwargs):
        category = MenuCategory.objects.create(restaurant=restaurant, **kwargs)
        # Set translation
        category.set_current_language("en")
        category.name = name
        category.save()
        return category

    return _create_category


@pytest.fixture
def menu_category(create_menu_category, restaurant):
    """Create a test menu category."""
    return create_menu_category(restaurant=restaurant, name="Appetizers")


@pytest.fixture
def create_menu_item(db):
    """Factory fixture to create menu items."""
    from decimal import Decimal

    from apps.menu.models import MenuItem

    def _create_item(restaurant, category=None, name="Test Item", price=Decimal("10.00"), **kwargs):
        item = MenuItem.objects.create(restaurant=restaurant, category=category, price=price, **kwargs)
        # Set translation
        item.set_current_language("en")
        item.name = name
        item.save()
        return item

    return _create_item


@pytest.fixture
def menu_item(create_menu_item, restaurant, menu_category):
    """Create a test menu item."""
    return create_menu_item(
        restaurant=restaurant,
        category=menu_category,
        name="Test Dish",
    )


@pytest.fixture
def create_modifier_group(db):
    """Factory fixture to create modifier groups."""
    from apps.menu.models import ModifierGroup

    def _create_group(restaurant, name="Test Modifiers", **kwargs):
        group = ModifierGroup.objects.create(restaurant=restaurant, **kwargs)
        # Set translation
        group.set_current_language("en")
        group.name = name
        group.save()
        return group

    return _create_group


@pytest.fixture
def modifier_group(create_modifier_group, restaurant):
    """Create a test modifier group."""
    return create_modifier_group(restaurant=restaurant, name="Size")


# ============== Table Fixtures ==============


@pytest.fixture
def create_table_section(db):
    """Factory fixture to create table sections."""
    from apps.tables.models import TableSection

    def _create_section(restaurant, name="Main Hall", **kwargs):
        section = TableSection.objects.create(restaurant=restaurant, name=name, **kwargs)
        return section

    return _create_section


@pytest.fixture
def table_section(create_table_section, restaurant):
    """Create a test table section."""
    return create_table_section(restaurant=restaurant, name="Main Hall")


@pytest.fixture
def create_table(db):
    """Factory fixture to create tables."""
    from apps.tables.models import Table

    def _create_table(restaurant, number="1", section=None, capacity=4, **kwargs):
        table = Table.objects.create(
            restaurant=restaurant,
            number=number,
            section=section,
            capacity=capacity,
            **kwargs,
        )
        return table

    return _create_table


@pytest.fixture
def table(create_table, restaurant, table_section):
    """Create a test table."""
    return create_table(restaurant=restaurant, number="T1", section=table_section)


@pytest.fixture
def create_qr_code(db):
    """Factory fixture to create QR codes."""
    from apps.tables.models import TableQRCode

    def _create_qr_code(table, code=None, **kwargs):
        if code is None:
            import uuid

            code = str(uuid.uuid4())[:8]
        qr = TableQRCode.objects.create(table=table, code=code, **kwargs)
        return qr

    return _create_qr_code


@pytest.fixture
def table_qr_code(create_qr_code, table):
    """Create a test QR code."""
    return create_qr_code(table=table, code="testqr123")


@pytest.fixture
def create_table_session(db):
    """Factory fixture to create table sessions."""
    from apps.tables.models import TableSession

    def _create_session(table, **kwargs):
        session = TableSession.objects.create(table=table, **kwargs)
        return session

    return _create_session


@pytest.fixture
def table_session(create_table_session, table):
    """Create a test table session."""
    return create_table_session(table=table, guest_count=2)


@pytest.fixture
def table_session_with_host(table, user):
    """Create a table session with a host user."""
    from apps.tables.models import TableSession, TableSessionGuest

    session = TableSession.objects.create(table=table, host=user, guest_count=1)
    # Create host as guest
    TableSessionGuest.objects.create(session=session, user=user, is_host=True)
    return session


@pytest.fixture
def create_session_guest(db):
    """Factory fixture to create session guests."""
    from apps.tables.models import TableSessionGuest

    def _create_guest(session, user=None, guest_name="", is_host=False, **kwargs):
        guest = TableSessionGuest.objects.create(
            session=session,
            user=user,
            guest_name=guest_name,
            is_host=is_host,
            **kwargs,
        )
        return guest

    return _create_guest


# ============== Order Fixtures ==============


@pytest.fixture
def create_order(db):
    """Factory fixture to create orders."""
    from apps.orders.models import Order

    def _create_order(restaurant, table=None, **kwargs):
        order = Order.objects.create(
            restaurant=restaurant,
            table=table,
            **kwargs,
        )
        return order

    return _create_order


@pytest.fixture
def order(create_order, restaurant, table):
    """Create a test order."""
    return create_order(restaurant=restaurant, table=table, order_type="dine_in")


@pytest.fixture
def create_order_item(db):
    """Factory fixture to create order items."""
    from decimal import Decimal

    from apps.orders.models import OrderItem

    def _create_order_item(order, menu_item=None, item_name="Test Item", unit_price=None, quantity=1, **kwargs):
        if unit_price is None:
            unit_price = menu_item.price if menu_item else Decimal("10.00")
        order_item = OrderItem.objects.create(
            order=order,
            menu_item=menu_item,
            item_name=item_name,
            unit_price=unit_price,
            quantity=quantity,
            total_price=unit_price * quantity,
            **kwargs,
        )
        return order_item

    return _create_order_item


@pytest.fixture
def order_item(create_order_item, order, menu_item):
    """Create a test order item."""
    return create_order_item(order=order, menu_item=menu_item, item_name=menu_item.name)


# ============== Payment Fixtures ==============


@pytest.fixture
def create_payment(db):
    """Factory fixture to create payments."""
    from decimal import Decimal

    from apps.payments.models import Payment

    def _create_payment(order, amount=Decimal("50.00"), tip_amount=Decimal("5.00"), **kwargs):
        payment = Payment.objects.create(
            order=order,
            amount=amount,
            tip_amount=tip_amount,
            total_amount=amount + tip_amount,
            **kwargs,
        )
        return payment

    return _create_payment


@pytest.fixture
def payment(create_payment, order):
    """Create a test payment."""
    return create_payment(order=order, payment_method="card", status="completed")


@pytest.fixture
def create_refund(db):
    """Factory fixture to create refunds."""
    from decimal import Decimal

    from apps.payments.models import Refund

    def _create_refund(payment, amount=Decimal("10.00"), reason="customer_request", **kwargs):
        refund = Refund.objects.create(
            payment=payment,
            amount=amount,
            reason=reason,
            **kwargs,
        )
        return refund

    return _create_refund


@pytest.fixture
def refund(create_refund, payment):
    """Create a test refund."""
    return create_refund(payment=payment)


@pytest.fixture
def create_payment_method(db):
    """Factory fixture to create payment methods."""
    from apps.payments.models import PaymentMethod

    def _create_method(
        customer,
        external_method_id="pm_test123",
        method_type="card",
        card_brand="visa",
        card_last4="4242",
        card_exp_month=12,
        card_exp_year=2025,
        **kwargs,
    ):
        method = PaymentMethod.objects.create(
            customer=customer,
            external_method_id=external_method_id,
            method_type=method_type,
            card_brand=card_brand,
            card_last4=card_last4,
            card_exp_month=card_exp_month,
            card_exp_year=card_exp_year,
            **kwargs,
        )
        return method

    return _create_method


@pytest.fixture
def payment_method(create_payment_method, user):
    """Create a test payment method."""
    return create_payment_method(customer=user, is_default=True)


# ============== Reservation Fixtures ==============


@pytest.fixture
def create_reservation_settings(db):
    """Factory fixture to create reservation settings."""
    from apps.reservations.models import ReservationSettings

    def _create_settings(restaurant, **kwargs):
        settings = ReservationSettings.objects.create(restaurant=restaurant, **kwargs)
        return settings

    return _create_settings


@pytest.fixture
def reservation_settings(create_reservation_settings, restaurant):
    """Create test reservation settings."""
    return create_reservation_settings(restaurant=restaurant)


@pytest.fixture
def create_reservation(db):
    """Factory fixture to create reservations."""
    from datetime import time, timedelta

    from django.utils import timezone

    from apps.reservations.models import Reservation

    def _create_reservation(
        restaurant,
        guest_name="Test Guest",
        guest_phone="+1234567890",
        reservation_date=None,
        reservation_time=time(19, 0),
        party_size=4,
        status="confirmed",
        customer=None,
        **kwargs,
    ):
        if reservation_date is None:
            reservation_date = timezone.now().date() + timedelta(days=1)
        reservation = Reservation.objects.create(
            restaurant=restaurant,
            guest_name=guest_name,
            guest_phone=guest_phone,
            reservation_date=reservation_date,
            reservation_time=reservation_time,
            party_size=party_size,
            status=status,
            customer=customer,
            **kwargs,
        )
        return reservation

    return _create_reservation


@pytest.fixture
def reservation(create_reservation, restaurant):
    """Create a test reservation."""
    return create_reservation(restaurant=restaurant)


@pytest.fixture
def create_blocked_time(db):
    """Factory fixture to create blocked times."""
    from datetime import timedelta

    from django.utils import timezone

    from apps.reservations.models import ReservationBlockedTime

    def _create_blocked_time(
        restaurant,
        start_datetime=None,
        end_datetime=None,
        reason="other",
        **kwargs,
    ):
        if start_datetime is None:
            start_datetime = timezone.now() + timedelta(days=1)
        if end_datetime is None:
            end_datetime = start_datetime + timedelta(hours=4)
        blocked = ReservationBlockedTime.objects.create(
            restaurant=restaurant,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            reason=reason,
            **kwargs,
        )
        return blocked

    return _create_blocked_time


@pytest.fixture
def blocked_time(create_blocked_time, restaurant):
    """Create a test blocked time."""
    return create_blocked_time(restaurant=restaurant, reason="holiday")


# ============== Restaurant Category Fixtures ==============


@pytest.fixture
def create_restaurant_category(db):
    """Factory fixture to create restaurant categories."""
    from apps.tenants.models import RestaurantCategory

    def _create_category(name="Test Category", slug=None, **kwargs):
        category = RestaurantCategory.objects.create(slug=slug or name.lower().replace(" ", "-"), **kwargs)
        # Set translation for Georgian (default)
        category.set_current_language("ka")
        category.name = name
        category.description = kwargs.get("description_ka", "")
        category.save()
        return category

    return _create_category


@pytest.fixture
def restaurant_category(create_restaurant_category):
    """Create a test restaurant category."""
    return create_restaurant_category(name="რესტორანი", slug="restaurant")


@pytest.fixture
def restaurant_category_with_translations(db):
    """Create a restaurant category with multiple language translations."""
    from apps.tenants.models import RestaurantCategory

    category = RestaurantCategory.objects.create(slug="georgian-cuisine", icon="restaurant")

    # Georgian translation
    category.set_current_language("ka")
    category.name = "ქართული სამზარეულო"
    category.description = "ტრადიციული ქართული კერძები"
    category.save()

    # English translation
    category.set_current_language("en")
    category.name = "Georgian Cuisine"
    category.description = "Traditional Georgian dishes"
    category.save()

    # Russian translation
    category.set_current_language("ru")
    category.name = "Грузинская кухня"
    category.description = "Традиционные грузинские блюда"
    category.save()

    return category


# ============== Amenity Fixtures ==============


@pytest.fixture
def create_amenity(db):
    """Factory fixture to create amenities."""
    from apps.tenants.models import Amenity

    def _create_amenity(name="Test Amenity", slug=None, **kwargs):
        amenity = Amenity.objects.create(slug=slug or name.lower().replace(" ", "-"), **kwargs)
        # Set translation for Georgian (default)
        amenity.set_current_language("ka")
        amenity.name = name
        amenity.description = kwargs.get("description_ka", "")
        amenity.save()
        return amenity

    return _create_amenity


@pytest.fixture
def amenity(create_amenity):
    """Create a test amenity."""
    return create_amenity(name="ტერასა", slug="terrace", icon="deck")


@pytest.fixture
def amenity_with_translations(db):
    """Create an amenity with multiple language translations."""
    from apps.tenants.models import Amenity

    amenity = Amenity.objects.create(slug="live-music", icon="music_note")

    # Georgian translation
    amenity.set_current_language("ka")
    amenity.name = "ცოცხალი მუსიკა"
    amenity.description = "ცოცხალი მუსიკალური გამოსვლები"
    amenity.save()

    # English translation
    amenity.set_current_language("en")
    amenity.name = "Live Music"
    amenity.description = "Live musical performances"
    amenity.save()

    # Russian translation
    amenity.set_current_language("ru")
    amenity.name = "Живая музыка"
    amenity.description = "Живые музыкальные выступления"
    amenity.save()

    return amenity


@pytest.fixture
def restaurant_with_category_and_amenities(restaurant, restaurant_category, amenity):
    """Create a restaurant with category and amenities assigned."""
    restaurant.category = restaurant_category
    restaurant.save()
    restaurant.amenities.add(amenity)
    return restaurant


# ============== Modifier with Translations Fixture ==============


@pytest.fixture
def create_modifier(db):
    """Factory fixture to create modifiers with translations."""
    from decimal import Decimal

    from apps.menu.models import Modifier

    def _create_modifier(group, name="Test Modifier", price_adjustment=Decimal("0"), **kwargs):
        modifier = Modifier.objects.create(group=group, price_adjustment=price_adjustment, **kwargs)
        modifier.set_current_language("en")
        modifier.name = name
        modifier.save()
        return modifier

    return _create_modifier


@pytest.fixture
def modifier_with_translations(modifier_group):
    """Create a modifier with multiple translations."""
    from decimal import Decimal

    from apps.menu.models import Modifier

    modifier = Modifier.objects.create(group=modifier_group, price_adjustment=Decimal("2.00"))

    modifier.set_current_language("ka")
    modifier.name = "დიდი"
    modifier.save()

    modifier.set_current_language("en")
    modifier.name = "Large"
    modifier.save()

    modifier.set_current_language("ru")
    modifier.name = "Большой"
    modifier.save()

    return modifier
