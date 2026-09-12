"""Suppliers, PO from the buy list, sending, receiving into lots, prices, admin pages, API, notifications."""

from decimal import Decimal

from django.core import mail
from django.test import Client
from django.utils import timezone

import pytest

from apps.inventory.models import StockLot
from apps.purchasing import services
from apps.purchasing.models import PurchaseOrder, Supplier, SupplierItem


@pytest.fixture
def purchasing(wh):
    wh.purchasing_enabled = True
    wh.save(update_fields=["purchasing_enabled"])
    return wh


@pytest.fixture
def farm(purchasing, flour, units):
    sup = Supplier.objects.create(
        restaurant=purchasing, name="Farm", email="farm@example.com", phone="+995555000001", lead_days=2
    )
    SupplierItem.objects.create(
        supplier=sup, stock_item=flour, unit=units["kg"], price=Decimal("2.5"), is_preferred=True
    )
    return sup


@pytest.mark.django_db
class TestOrders:
    def test_from_buy_list_and_lifecycle(
        self, purchasing, farm, flour, cheese, units, user, django_capture_on_commit_callbacks
    ):
        # flour: par 2000 g, nothing on hand -> 2 kg = 1 pack of 10 kg; cheese has no supplier -> unassigned PO
        orders = services.orders_from_buy_list(purchasing, by=user)
        by_supplier = {o.supplier_id: o for o in orders}
        po = by_supplier[farm.pk]
        assert (
            po.status == "draft"
            and po.number.startswith("PO-")
            and po.expected_on == timezone.localdate() + timezone.timedelta(days=1)
        )
        line = po.lines.get()
        assert (
            line.stock_item == flour
            and line.unit == units["kg"]
            and line.quantity == Decimal("10")
            and line.unit_price == Decimal("2.5")
        )
        assert po.subtotal == Decimal("25.00") and "out" in line.note
        assert None in by_supplier  # cheese
        text = services.render_text(po)
        assert "Flour: 10 kg @ 2.50" in text and po.number in text
        # send -> email through the notifications module
        with django_capture_on_commit_callbacks(execute=True):
            result = services.send_order(po, by=user)
        po.refresh_from_db()
        assert result["via"] == "email" and po.status == "sent" and po.sent_via == "email"
        assert len(mail.outbox) == 1 and po.number in mail.outbox[0].subject and "Flour" in mail.outbox[0].body
        # partial receive at a better price
        lots = services.receive_order(
            po, [{"line": line, "quantity": Decimal("4"), "unit_price": Decimal("2.4")}], by=user, reference="INV-9"
        )
        po.refresh_from_db()
        line.refresh_from_db()
        assert po.status == "partial" and line.received_qty == Decimal("4") and po.reference == "INV-9"
        lot = lots[0]
        assert lot.received_qty == Decimal("4000") and lot.supplier_name == "Farm" and lot.reference == "INV-9"
        assert lot.unit_cost == Decimal("0.0024")  # 2.4 per kg -> per g
        si = SupplierItem.objects.get(supplier=farm, stock_item=flour)
        assert si.price == Decimal("2.4") and si.history.count() == 1
        flour.refresh_from_db()
        assert flour.on_hand_qty == Decimal("4000")
        # the rest arrives
        services.receive_order(po, [{"line_id": line.pk, "quantity": Decimal("6")}], by=user)
        po.refresh_from_db()
        assert po.status == "received" and po.lines.get().outstanding == 0
        assert StockLot.objects.filter(stock_item=flour).count() == 2
        with pytest.raises(services.PurchasingError):
            services.receive_order(po, [{"line_id": line.pk, "quantity": 1}], by=user)
        with pytest.raises(services.PurchasingError):
            services.cancel_order(po, by=user)
        summary = services.open_summary(purchasing)
        assert summary["open"] == 1 and summary["received_this_month"] == Decimal("24.00")
        report = services.supplier_report(
            purchasing, timezone.now() - timezone.timedelta(days=1), timezone.now() + timezone.timedelta(days=1)
        )
        assert report[0]["supplier"] == "Farm" and report[0]["lots"] == 2

    def test_manual_send_and_cancel(self, purchasing, flour, units, user):
        sup = Supplier.objects.create(restaurant=purchasing, name="Cash & carry")
        po = services.create_order(
            purchasing, supplier=sup, lines=[{"stock_item": flour, "quantity": 3, "unit": units["kg"]}], by=user
        )
        assert po.lines.get().unit_price == 0 and po.expected_on == timezone.localdate() + timezone.timedelta(days=1)
        result = services.send_order(po, by=user)
        assert result["via"] == "manual" and result["email"] is None
        services.cancel_order(po, by=user)
        assert po.status == "cancelled"
        with pytest.raises(services.PurchasingError):
            services.send_order(po, by=user)

    def test_backfill_and_due_notifications(self, purchasing, flour, user, django_capture_on_commit_callbacks):
        flour.supplier_name = "Old Farm"
        flour.save()
        assert services.backfill_suppliers(purchasing) == 1
        flour.refresh_from_db()
        assert flour.supplier.name == "Old Farm"
        assert services.backfill_suppliers(purchasing) == 0
        po = services.create_order(
            purchasing,
            supplier=flour.supplier,
            lines=[{"stock_item": flour, "quantity": 1, "unit": flour.purchase_unit}],
            by=user,
            expected_on=timezone.localdate(),
        )
        services.send_order(po, by=user)
        from apps.notifications.models import Notification

        with django_capture_on_commit_callbacks(execute=True):
            assert services.notify_due() == 1
        assert Notification.objects.filter(event="purchasing.po_due", user=user).exists()


@pytest.mark.django_db
class TestAdminAndApi:
    def test_admin_pages(self, purchasing, farm, flour, units, user, staff_roles, create_staff_member):
        create_staff_member(user=user, restaurant=purchasing, role=next(r for r in staff_roles if r.name == "owner"))
        c = Client(HTTP_HOST=f"{purchasing.slug}.localhost")
        c.force_login(user)
        assert c.get("/tenant-admin/purchasing/supplier/").status_code == 200
        page = c.get("/tenant-admin/purchasing/purchaseorder/")
        assert page.status_code == 200 and 'data-testid="po-from-buy-list"' in page.content.decode()
        # the warehouse overview offers the same button
        overview = c.get("/tenant-admin/inventory/warehouseoverview/").content.decode()
        assert 'data-testid="buy-list-to-po"' in overview
        res = c.post("/tenant-admin/purchasing/purchaseorder/from-buy-list/", {})
        assert res.status_code == 302
        po = PurchaseOrder.objects.get(supplier=farm)
        assert c.get(f"/tenant-admin/purchasing/purchaseorder/{po.pk}/change/").status_code == 200
        assert (
            c.get(f"/tenant-admin/purchasing/purchaseorder/{po.pk}/text/").content.decode().startswith("Purchase order")
        )
        assert c.get(f"/tenant-admin/purchasing/purchaseorder/{po.pk}/send/").status_code == 302
        po.refresh_from_db()
        assert po.status == "sent"
        page = c.get(f"/tenant-admin/purchasing/purchaseorder/{po.pk}/receive/")
        assert page.status_code == 200 and 'data-testid="po-receive"' in page.content.decode()
        line = po.lines.get()
        res = c.post(
            f"/tenant-admin/purchasing/purchaseorder/{po.pk}/receive/",
            {f"qty_{line.pk}": "10", f"price_{line.pk}": "2.6", "reference": "WB-1"},
        )
        assert res.status_code == 302
        po.refresh_from_db()
        assert po.status == "received" and StockLot.objects.filter(stock_item=flour, reference="WB-1").exists()
        # the supplier's price list learned the new price
        assert SupplierItem.objects.get(supplier=farm, stock_item=flour).price == Decimal("2.6")
        purchasing.purchasing_enabled = False
        purchasing.save(update_fields=["purchasing_enabled"])
        assert c.get("/tenant-admin/purchasing/purchaseorder/").status_code == 403

    def test_api(self, authenticated_owner_client, purchasing, farm, flour, user, staff_roles, create_staff_member):
        create_staff_member(user=user, restaurant=purchasing, role=next(r for r in staff_roles if r.name == "owner"))
        api = authenticated_owner_client
        api.defaults["HTTP_X_RESTAURANT"] = purchasing.slug
        res = api.get("/api/v1/dashboard/purchasing/suppliers/")
        assert res.status_code == 200 and res.json()["results"][0]["name"] == "Farm"
        res = api.post("/api/v1/dashboard/purchasing/orders/from-buy-list/")
        assert res.status_code == 201 and len(res.json()) >= 1
        po = next(o for o in res.json() if o["supplier_name"] == "Farm")
        line = po["lines"][0]
        res = api.post(
            f"/api/v1/dashboard/purchasing/orders/{po['id']}/receive/",
            {"reference": "API-1", "lines": [{"line_id": line["id"], "quantity": "10"}]},
            format="json",
        )
        assert res.status_code == 200, res.content
        assert res.json()["status"] == "received"
        res = api.get("/api/v1/dashboard/purchasing/orders/?status=open")
        assert res.status_code == 200 and all(o["status"] != "received" for o in res.json()["results"])
