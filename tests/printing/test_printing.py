"""Rendering, ESC/POS bytes, queue + bridge API, hooks and the dashboard API."""

import base64
import threading
from decimal import Decimal

from django.db import connection
from django.utils import timezone

import pytest

from apps.orders import services as order_services
from apps.printing import escpos, render, services
from apps.printing.models import Printer, PrintJob
from apps.printing.receipts import receipt_data


@pytest.fixture
def printing_restaurant(restaurant):
    restaurant.printing_enabled = True
    restaurant.cash_enabled = False
    restaurant.save(update_fields=["printing_enabled", "cash_enabled"])
    return restaurant


@pytest.fixture
def kitchen_printer(printing_restaurant):
    return Printer.objects.create(
        restaurant=printing_restaurant, name="Kitchen pass", kind="kitchen", stations="kitchen"
    )


@pytest.fixture
def bar_printer(printing_restaurant):
    return Printer.objects.create(restaurant=printing_restaurant, name="Bar", kind="bar", stations="bar", paper="58")


@pytest.fixture
def receipt_printer(printing_restaurant):
    return Printer.objects.create(
        restaurant=printing_restaurant, name="Till", kind="receipt", stations="kitchen", open_drawer=True
    )


@pytest.fixture
def order(printing_restaurant, table, create_order, create_order_item, menu_item):
    o = create_order(restaurant=printing_restaurant, table=table, customer_notes="ალერგია თხილზე")
    create_order_item(
        order=o,
        menu_item=menu_item,
        item_name="ხინკალი",
        unit_price=Decimal("2.50"),
        quantity=6,
        preparation_station="kitchen",
        special_instructions="ცხარე",
    )
    create_order_item(order=o, item_name="ლიმონათი", unit_price=Decimal("4"), quantity=2, preparation_station="bar")
    o.calculate_totals()
    return o


@pytest.mark.django_db
class TestRender:
    def test_ticket_image_width_and_georgian_glyphs(self, order, kitchen_printer, bar_printer):
        img = render.render_ticket(order, list(order.items.all()), printer=kitchen_printer, reason="new")
        assert img.width == 576 and img.height > 200
        dark = sum(1 for p in img.getdata() if p < 128)
        assert dark > 2000  # text really got drawn (Noto Sans Georgian has the glyphs)
        small = render.render_ticket(order, list(order.items.all()), printer=bar_printer)
        assert small.width == 384

    def test_receipt_and_test_page(self, order, receipt_printer):
        data = receipt_data(order)
        assert data["order"]["order_number"] == order.order_number and len(data["lines"]) == 2
        assert data["totals"]["total"] == str(order.total)
        img = render.render_receipt(data, printer=receipt_printer)
        assert img.width == 576
        assert render.render_test(receipt_printer, receipt_printer.restaurant).height > 100

    def test_escpos_bytes(self, order, kitchen_printer):
        img = render.render_ticket(order, list(order.items.all()), printer=kitchen_printer)
        data = escpos.image_to_escpos(img, paper="80", cut=True, drawer=True)
        assert data.startswith(b"\x1b@")
        assert b"\x1dv0" in data
        assert data.endswith(escpos.PARTIAL_CUT + escpos.DRAWER_KICK)
        narrow = escpos.image_to_escpos(img, paper="58")
        # 384 px wide -> 48 bytes per row in the raster header
        assert b"\x1dv0\x00\x30\x00" in narrow


@pytest.mark.django_db
class TestQueue:
    def test_ticket_routing_by_station(self, order, kitchen_printer, bar_printer, receipt_printer):
        jobs = services.enqueue_ticket(order)
        by_printer = {j.printer_id: j for j in jobs}
        assert set(by_printer) == {kitchen_printer.pk, bar_printer.pk}
        assert by_printer[kitchen_printer.pk].payload["items"] == [str(order.items.get(item_name="ხინკალი").pk)]
        assert by_printer[bar_printer.pk].payload["items"] == [str(order.items.get(item_name="ლიმონათი").pk)]
        both = Printer.objects.create(restaurant=order.restaurant, name="Pass", kind="kitchen", stations="both")
        jobs = services.enqueue_ticket(order, printers=[both])
        assert len(jobs[0].payload["items"]) == 2

    def test_module_off_means_no_jobs(self, order, kitchen_printer):
        order.restaurant.printing_enabled = False
        order.restaurant.save(update_fields=["printing_enabled"])
        assert services.enqueue_ticket(order) == []

    def test_confirm_hook_and_added_items(self, order, kitchen_printer, user, menu_item):
        order_services.transition_order(order, "confirmed", by=user)
        assert PrintJob.objects.filter(order=order, kind="ticket").count() == 1
        assert PrintJob.objects.get(order=order).payload["reason"] == "new"
        kitchen_printer.auto_print = False
        kitchen_printer.save()
        o2_items = list(order.items.all())
        from apps.printing import hooks

        hooks.on_items_added(order, o2_items, by=user)
        assert PrintJob.objects.filter(order=order).count() == 1  # auto_print off

    def test_receipt_hook_on_cash_payment(self, order, receipt_printer, user):
        from apps.payments import services as ledger

        ledger.record_payment(
            order.restaurant, method="cash", amount=order.total, tendered=Decimal("50"), order=order, by=user
        )
        job = PrintJob.objects.get(kind="receipt")
        assert job.printer == receipt_printer and job.payload["payment"]["change"]
        assert job.escpos.endswith(escpos.PARTIAL_CUT + escpos.DRAWER_KICK)  # cash + open_drawer

    def test_claim_ack_fail_requeue(self, order, kitchen_printer):
        job = services.enqueue_ticket(order)[0]
        claimed = services.claim_next(kitchen_printer)
        assert claimed.pk == job.pk and claimed.status == "printing" and claimed.attempts == 1
        assert services.claim_next(kitchen_printer) is None
        services.mark_failed(claimed, "paper out")
        claimed.refresh_from_db()
        assert claimed.status == "queued" and claimed.error == "paper out"
        kitchen_printer.refresh_from_db()
        assert kitchen_printer.last_error == "paper out" and kitchen_printer.is_online
        for _ in range(2):
            services.mark_failed(services.claim_next(kitchen_printer), "paper out")
        claimed.refresh_from_db()
        assert claimed.status == "failed" and claimed.attempts == 3
        services.retry(claimed)
        assert services.claim_next(kitchen_printer).pk == claimed.pk
        services.mark_done(claimed)
        claimed.refresh_from_db()
        assert claimed.status == "done" and claimed.printed_at

    def test_requeue_stale(self, order, kitchen_printer):
        job = services.enqueue_ticket(order)[0]
        services.claim_next(kitchen_printer)
        PrintJob.objects.filter(pk=job.pk).update(claimed_at=timezone.now() - timezone.timedelta(minutes=5))
        assert services.requeue_stale() == 1
        job.refresh_from_db()
        assert job.status == "queued"


@pytest.mark.django_db(transaction=True)
class TestClaimRace:
    def test_two_bridges_one_claim(self, restaurant, table, create_order, create_order_item, menu_item):
        restaurant.printing_enabled = True
        restaurant.save(update_fields=["printing_enabled"])
        printer = Printer.objects.create(restaurant=restaurant, name="K", kind="kitchen")
        o = create_order(restaurant=restaurant, table=table)
        create_order_item(order=o, menu_item=menu_item, item_name="x", unit_price=Decimal("1"))
        services.enqueue_ticket(o)
        results = []
        barrier = threading.Barrier(2)

        def worker():
            try:
                barrier.wait(timeout=5)
                results.append(services.claim_next(printer))
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sum(1 for r in results if r is not None) == 1


@pytest.mark.django_db
class TestBridgeApi:
    def test_next_done_and_auth(self, api_client, order, kitchen_printer):
        job = services.enqueue_ticket(order)[0]
        assert api_client.get("/api/v1/print-bridge/jobs/next/").status_code == 401
        res = api_client.get("/api/v1/print-bridge/jobs/next/", HTTP_X_BRIDGE_KEY=kitchen_printer.bridge_key)
        assert res.status_code == 200
        body = res.json()
        assert body["id"] == str(job.pk) and body["copies"] == 1
        assert base64.b64decode(body["escpos_b64"]).startswith(b"\x1b@")
        res = api_client.get("/api/v1/print-bridge/jobs/next/?wait=0", HTTP_X_BRIDGE_KEY=kitchen_printer.bridge_key)
        assert res.status_code == 204
        res = api_client.post(f"/api/v1/print-bridge/jobs/{job.pk}/done/", HTTP_X_BRIDGE_KEY=kitchen_printer.bridge_key)
        assert res.status_code == 200 and res.json()["status"] == "done"
        res = api_client.get("/api/v1/print-bridge/ping/", HTTP_X_BRIDGE_KEY=kitchen_printer.bridge_key)
        assert res.status_code == 200 and res.json()["printer"] == "Kitchen pass"

    def test_failed_and_other_printers_job(self, api_client, order, kitchen_printer, bar_printer):
        jobs = {j.printer_id: j for j in services.enqueue_ticket(order)}
        res = api_client.post(
            f"/api/v1/print-bridge/jobs/{jobs[bar_printer.pk].pk}/failed/",
            {"error": "jam"},
            format="json",
            HTTP_X_BRIDGE_KEY=kitchen_printer.bridge_key,
        )
        assert res.status_code == 404
        res = api_client.post(
            f"/api/v1/print-bridge/jobs/{jobs[bar_printer.pk].pk}/failed/",
            {"error": "jam"},
            format="json",
            HTTP_X_BRIDGE_KEY=bar_printer.bridge_key,
        )
        assert res.status_code == 200 and res.json()["status"] == "queued"


@pytest.mark.django_db
class TestDashboardApi:
    def test_printers_crud_and_jobs(self, authenticated_owner_client, printing_restaurant, order, kitchen_printer):
        c = authenticated_owner_client
        c.defaults["HTTP_X_RESTAURANT"] = printing_restaurant.slug
        res = c.post(
            "/api/v1/dashboard/printing/printers/",
            {"name": "Till", "kind": "receipt", "paper": "80", "connection": "bridge"},
            format="json",
        )
        assert res.status_code == 201, res.content
        data = res.json().get("data") or res.json()
        assert data["bridge_key"]
        res = c.post(
            "/api/v1/dashboard/printing/jobs/create/", {"kind": "ticket", "order_id": str(order.id)}, format="json"
        )
        assert res.status_code == 201
        assert (res.json().get("data") or res.json())[0]["printer_name"] == "Kitchen pass"
        res = c.post(
            "/api/v1/dashboard/printing/jobs/create/", {"kind": "receipt", "order_id": str(order.id)}, format="json"
        )
        assert res.status_code == 201
        res = c.get("/api/v1/dashboard/printing/jobs/?status=queued")
        assert res.status_code == 200 and res.json()["count"] == 2
        res = c.post(f"/api/v1/dashboard/printing/printers/{kitchen_printer.id}/test/", {}, format="json")
        assert res.status_code == 201

    def test_no_printer_409_and_module_off(self, authenticated_owner_client, printing_restaurant, order):
        c = authenticated_owner_client
        c.defaults["HTTP_X_RESTAURANT"] = printing_restaurant.slug
        res = c.post(
            "/api/v1/dashboard/printing/jobs/create/", {"kind": "receipt", "order_id": str(order.id)}, format="json"
        )
        assert res.status_code == 409
        printing_restaurant.printing_enabled = False
        printing_restaurant.save(update_fields=["printing_enabled"])
        assert c.get("/api/v1/dashboard/printing/printers/").status_code == 403

    def test_waiter_reads_printers_but_cannot_create(
        self, authenticated_waiter_client, waiter_staff, printing_restaurant, kitchen_printer
    ):
        c = authenticated_waiter_client
        c.defaults["HTTP_X_RESTAURANT"] = printing_restaurant.slug
        res = c.get("/api/v1/dashboard/printing/printers/")
        assert res.status_code == 200
        assert "bridge_key" not in res.json()["results"][0]
        assert (
            c.post("/api/v1/dashboard/printing/printers/", {"name": "x", "kind": "kitchen"}, format="json").status_code
            == 403
        )


@pytest.mark.django_db
class TestAdminPages:
    def test_pages_render_with_setup_snippet(
        self, user, printing_restaurant, staff_roles, create_staff_member, kitchen_printer
    ):
        from django.test import Client

        create_staff_member(
            user=user, restaurant=printing_restaurant, role=next(r for r in staff_roles if r.name == "owner")
        )
        user.is_staff = True
        user.save()
        c = Client(HTTP_HOST=f"{printing_restaurant.slug}.localhost")
        c.force_login(user)
        assert c.get("/tenant-admin/printing/printer/").status_code == 200
        html = c.get(f"/tenant-admin/printing/printer/{kitchen_printer.pk}/change/").content.decode()
        assert kitchen_printer.bridge_key in html and 'data-testid="bridge-setup"' in html
        assert c.get("/tenant-admin/printing/printjob/").status_code == 200
        dash = c.get("/tenant-admin/").content.decode()
        assert 'data-testid="card-printing"' in dash
