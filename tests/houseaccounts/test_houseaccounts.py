"""House accounts: charge / limit / settle / adjust / close, statements, monthly beat, overdue, API, admin, report."""

from datetime import date, timedelta
from decimal import Decimal

from django.test import Client
from django.utils import timezone

from rest_framework.test import APIClient

import pytest

from apps.houseaccounts import services
from apps.houseaccounts.models import HouseAccount, HouseAccountEntry, HouseAccountStatement
from apps.notifications.models import Notification, OutboundMessage
from apps.orders.models import Order, OrderItem
from apps.payments import services as ledger

D = "/api/v1/dashboard/house-accounts/"


@pytest.fixture(autouse=True)
def _env(settings):
    settings.MAIN_DOMAIN = "localhost"
    settings.SMS_PROVIDER = "console"


@pytest.fixture
def ha(restaurant):
    restaurant.house_accounts_enabled = True
    restaurant.cash_enabled = True
    restaurant.save()
    from apps.notifications import services as notifications

    cfg = notifications.settings_for(restaurant)
    cfg.guest_sms = True
    cfg.save()
    return restaurant


@pytest.fixture
def account(ha):
    return HouseAccount.objects.create(
        restaurant=ha, name="Giorgi", company="Acme LLC", phone="+995555777888", credit_limit=Decimal("100")
    )


@pytest.fixture
def shift(ha, user):
    return ledger.open_shift(ha, by=user, opening_float=Decimal("20"))


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


def bill(restaurant, menu_item, total="30.00"):
    o = Order.objects.create(restaurant=restaurant, order_type="dine_in", status="confirmed", source="pos")
    OrderItem.objects.create(
        order=o,
        menu_item=menu_item,
        item_name="Dish",
        unit_price=Decimal(total),
        quantity=1,
        total_price=Decimal(total),
    )
    o.calculate_totals()
    return o


@pytest.mark.django_db
class TestLedger:
    def test_charge_limit_settle(self, ha, account, shift, user, menu_item):
        order = bill(ha, menu_item)
        payment, entry = services.charge(account, "30", by=user, order=order, signed_by="Nino")
        account.refresh_from_db()
        assert (
            payment.payment_method == "house_account" and account.balance == Decimal("30.00") and ledger.is_paid(order)
        )
        assert entry.kind == "charge" and entry.balance_after == Decimal("30.00") and entry.signed_by == "Nino"
        with pytest.raises(services.HouseAccountError) as exc:
            services.charge(account, "80", by=user, order=bill(ha, menu_item, "80"))
        assert exc.value.code == "credit_limit" and exc.value.extra["available"] == "70.00"
        services.charge(account, "65", by=user, order=bill(ha, menu_item, "65"))
        assert Notification.objects.filter(title__startswith="House account Giorgi").exists()
        payment, entry = services.settle(account, "50", method="cash", by=user, tendered="50")
        account.refresh_from_db()
        assert (
            payment.order_id is None
            and payment.shift_id == shift.pk
            and account.balance == Decimal("45.00")
            and account.last_payment_at
        )
        with pytest.raises(services.HouseAccountError) as exc:
            services.set_status(account, "closed")
        assert exc.value.code == "balance_open"
        services.adjust(account, "-45", by=user, note="write off", writeoff=True)
        account.refresh_from_db()
        assert account.balance == 0
        services.set_status(account, "closed")
        with pytest.raises(services.HouseAccountError):
            services.charge(account, "1", by=user, order=bill(ha, menu_item, "1"))

    def test_statement_and_monthly(self, ha, account, shift, user, menu_item):
        services.charge(account, "30", by=user, order=bill(ha, menu_item))
        services.settle(account, "10", method="cash", by=user, tendered="10")
        today = timezone.localdate()
        stmt = services.build_statement(account, today.replace(day=1), today)
        assert (
            stmt.charges == Decimal("30.00")
            and stmt.payments == Decimal("10.00")
            and stmt.closing == Decimal("20.00")
            and len(stmt.lines) == 2
        )
        assert services.send_statement(stmt, force=True)
        assert OutboundMessage.objects.filter(kind="statement", to="+995555777888").exists()
        # monthly beat: billing day 1 -> previous month statement
        HouseAccountEntry.objects.filter(account=account).update(created_at=timezone.now() - timedelta(days=35))
        first = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
        assert services.monthly_statements(first) == 1
        assert HouseAccountStatement.objects.filter(account=account, sent_at__isnull=False).count() >= 1
        assert services.monthly_statements(first) == 0  # already sent
        rep = services.report(ha, today - timedelta(days=60), today)
        assert (
            rep["charged"] == Decimal("30.00")
            and rep["settled"] == Decimal("10.00")
            and rep["outstanding"] == Decimal("20.00")
        )

    def test_overdue(self, ha, account, shift, user, menu_item):
        services.charge(account, "30", by=user, order=bill(ha, menu_item))
        assert services.overdue(ha) == []
        HouseAccount.objects.filter(pk=account.pk).update(created_at=timezone.now() - timedelta(days=40))
        assert [a.pk for a in services.overdue(ha)] == [account.pk]


@pytest.mark.django_db
class TestApiAndAdmin:
    def test_dashboard_flow(self, ha, shift, owner_api, menu_item):
        res = owner_api.post(
            D,
            {
                "name": "Acme",
                "company": "Acme LLC",
                "phone": "555 12 34 56",
                "credit_limit": "200",
                "require_signature": True,
            },
            format="json",
        )
        assert res.status_code == 201, res.content
        aid = res.data["id"]
        assert owner_api.get(D + "lookup/?phone=555123456").data["id"] == aid
        order = bill(ha, menu_item)
        res = owner_api.post(D + f"{aid}/charge/", {"amount": "30", "order_id": str(order.pk)}, format="json")
        assert res.status_code == 409 and res.data["error"]["code"] == "signature_required"
        res = owner_api.post(
            D + f"{aid}/charge/", {"amount": "30", "order_id": str(order.pk), "signed_by": "Nino"}, format="json"
        )
        assert res.status_code == 200 and res.data["account"]["balance"] == "30.00" and res.data["balance"] == "0.00"
        res = owner_api.post(D + f"{aid}/settle/", {"amount": "10", "method": "cash", "tendered": "20"}, format="json")
        assert res.status_code == 200 and res.data["account"]["balance"] == "20.00" and res.data["change"] == "10.00"
        assert len(owner_api.get(D + f"{aid}/entries/").data) == 2
        res = owner_api.post(
            D + f"{aid}/statements/",
            {"period_start": str(date.today().replace(day=1)), "period_end": str(date.today()), "send": True},
            format="json",
        )
        assert res.status_code == 201 and res.data["closing"] == "20.00" and res.data["sent_to"]
        assert owner_api.get(D + "summary/").data["outstanding"] == "20.00"
        assert owner_api.patch(D + f"{aid}/", {"credit_limit": "500"}, format="json").data["credit_limit"] == "500.00"
        sid = res.data["id"]
        tok = HouseAccountStatement.objects.get(pk=sid).token
        html = APIClient().get(f"/api/v1/house-accounts/statement/{tok}/", HTTP_ACCEPT="text/html").content.decode()
        assert "Acme" in html and "20.00" in html

    def test_module_off(self, restaurant, owner_api):
        assert owner_api.get(D).status_code == 403

    def test_admin_pages(self, ha, account, shift, owner_admin, user, menu_item):
        services.charge(account, "30", by=user, order=bill(ha, menu_item))
        html = owner_admin.get("/tenant-admin/houseaccounts/houseaccount/").content.decode()
        assert "Giorgi" in html and 'data-testid="houseaccounts-summary"' in html
        html = owner_admin.get(f"/tenant-admin/houseaccounts/houseaccount/{account.pk}/settle-page/").content.decode()
        assert "30.00" in html
        assert (
            owner_admin.post(
                f"/tenant-admin/houseaccounts/houseaccount/{account.pk}/settle-page/",
                {"amount": "30", "method": "cash"},
            ).status_code
            == 302
        )
        account.refresh_from_db()
        assert account.balance == 0
        assert owner_admin.get(f"/tenant-admin/houseaccounts/houseaccount/{account.pk}/statement/").status_code == 302
        assert HouseAccountStatement.objects.filter(account=account).exists()
        assert "Giorgi" in owner_admin.get("/tenant-admin/houseaccounts/houseaccountstatement/").content.decode()
