"""VAT maths, order totals, numbering, documents through the null provider, RS.ge XML, admin and API."""

import re
from decimal import Decimal

from django.test import Client

import pytest

from apps.fiscal import services, vat
from apps.fiscal.models import FiscalDocument, FiscalProfile
from apps.fiscal.providers.base import ProviderResult
from apps.fiscal.providers.rsge.soap import RsGeWaybillClient, envelope
from apps.fiscal.providers.rsge.waybill_xml import build_waybill_xml
from apps.orders import services as order_services
from apps.payments import services as ledger


class TestVatMaths:
    def test_inclusive_split(self):
        assert vat.split_gross(Decimal("12.00"), Decimal("18")) == (Decimal("10.17"), Decimal("1.83"))
        assert vat.split_gross(Decimal("12.00"), 0) == (Decimal("12.00"), Decimal("0"))

    def test_exclusive_add(self):
        assert vat.add_vat(Decimal("10.00"), Decimal("18")) == (Decimal("11.80"), Decimal("1.80"))

    def test_allocate_largest_remainder(self):
        shares = vat.allocate(Decimal("1.00"), [1, 1, 1])
        assert shares == [Decimal("0.34"), Decimal("0.33"), Decimal("0.33")]
        assert sum(shares) == Decimal("1.00")
        assert vat.allocate(Decimal("5"), [0, 0]) == [Decimal("0"), Decimal("0")]


@pytest.fixture
def fiscal_restaurant(restaurant):
    restaurant.fiscal_enabled = True
    restaurant.cash_enabled = False
    restaurant.service_charge = Decimal("10")
    restaurant.save(update_fields=["fiscal_enabled", "cash_enabled", "service_charge"])
    FiscalProfile.objects.create(
        restaurant=restaurant,
        vat_payer=True,
        vat_rate=Decimal("18"),
        prices_include_vat=True,
        legal_name="შპს თიალი",
        tax_id="405123456",
    )
    return restaurant


@pytest.fixture
def order(fiscal_restaurant, table, create_order, create_order_item, menu_item):
    o = create_order(restaurant=fiscal_restaurant, table=table)
    create_order_item(order=o, menu_item=menu_item, item_name="ხინკალი", unit_price=Decimal("10"), quantity=1)
    create_order_item(order=o, item_name="ლიმონათი", unit_price=Decimal("2"), quantity=1)
    o.calculate_totals()
    o.refresh_from_db()
    return o


@pytest.mark.django_db
class TestTotals:
    def test_legacy_without_profile_unchanged(self, restaurant, table, create_order, create_order_item):
        restaurant.tax_rate = Decimal("10")
        restaurant.save(update_fields=["tax_rate"])
        o = create_order(restaurant=restaurant, table=table)
        create_order_item(order=o, item_name="x", unit_price=Decimal("10"))
        o.calculate_totals()
        o.refresh_from_db()
        assert (o.tax_amount, o.total, o.prices_include_vat, o.vat_rate) == (
            Decimal("1.00"),
            Decimal("11.00"),
            False,
            Decimal("10"),
        )

    def test_inclusive_profile_does_not_add_vat(self, order):
        # 12 gross + 10 % service = 13.20; VAT informational
        assert order.subtotal == Decimal("12.00")
        assert order.service_charge == Decimal("1.20")
        assert order.total == Decimal("13.20")
        assert order.tax_amount == Decimal("2.01")  # 13.20 - 13.20/1.18
        assert order.prices_include_vat is True and order.vat_rate == Decimal("18.00")

    def test_breakdown_sums_to_totals(self, order):
        bd = vat.vat_breakdown(order)
        assert [l["name"] for l in bd.lines] == ["1 × ხინკალი", "1 × ლიმონათი", "მომსახურება / Service"]
        assert bd.gross_total == order.total
        assert bd.net_total + bd.vat_total == bd.gross_total
        assert abs(bd.vat_total - order.tax_amount) <= Decimal("0.01") * len(bd.lines)
        partial = vat.vat_breakdown(order, amount=Decimal("6.60"))
        assert partial.gross_total == Decimal("6.60")

    def test_discount_spread_over_lines(self, order, user):
        order_services.apply_discount(order, mode="percent", value=50, by=user, reason_text="x")
        order.refresh_from_db()
        bd = vat.vat_breakdown(order)
        assert bd.gross_total == order.total
        assert Decimal(bd.lines[0]["gross"]) == Decimal("5.00")
        assert Decimal(bd.lines[2]["gross"]) == order.service_charge

    def test_non_payer(self, order):
        profile = order.restaurant.fiscal_profile
        profile.vat_payer = False
        profile.save()
        order.calculate_totals()
        order.refresh_from_db()
        assert order.tax_amount == Decimal("0") and order.total == Decimal("13.20")
        bd = vat.vat_breakdown(order)
        assert bd.breakdown == [] and bd.vat_total == Decimal("0") and "Not a VAT payer" in bd.label


@pytest.mark.django_db
class TestDocuments:
    def test_payment_creates_confirmed_receipt_once(self, order, user, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            p = ledger.record_payment(
                order.restaurant, method="card_terminal", amount=order.total, order=order, by=user
            )
        doc = FiscalDocument.objects.get(payment=p, kind="receipt")
        assert doc.status == "confirmed" and doc.is_fiscal is False and doc.provider == "none"
        assert doc.fiscal_number == "R-000001" and doc.external_id == "internal:R-000001"
        assert doc.gross_total == order.total and doc.vat_total > 0
        p.refresh_from_db()
        assert p.receipt_number == "R-000001"
        p.complete()  # webhook retry: no second document
        assert FiscalDocument.objects.filter(payment=p).count() == 1
        assert services.create_receipt(p).pk == doc.pk

    def test_module_off_no_document(self, order, user):
        order.restaurant.fiscal_enabled = False
        order.restaurant.save(update_fields=["fiscal_enabled"])
        ledger.record_payment(order.restaurant, method="card_terminal", amount=order.total, order=order, by=user)
        assert not FiscalDocument.objects.exists()

    def test_refund_and_cancel_reverse(self, order, user, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            p = ledger.record_payment(
                order.restaurant, method="card_terminal", amount=order.total, order=order, by=user
            )
            ledger.refund_payment(p, amount=Decimal("2.00"), by=user)
        refund_doc = FiscalDocument.objects.get(kind="refund")
        receipt = FiscalDocument.objects.get(kind="receipt")
        assert (
            refund_doc.reverses == receipt
            and refund_doc.gross_total == Decimal("2.00")
            and refund_doc.status == "confirmed"
        )
        assert refund_doc.fiscal_number.startswith("RF-")
        with django_capture_on_commit_callbacks(execute=True):
            order_services.transition_order(order, "cancelled", by=user, cancellation_reason="x")
        reversal = FiscalDocument.objects.filter(kind="refund", refund__isnull=True).get()
        assert reversal.reverses == receipt and reversal.gross_total == receipt.gross_total - Decimal("2.00")

    def test_stock_receipt_becomes_draft_waybill(self, fiscal_restaurant, user):
        from apps.inventory import services as inv
        from apps.inventory.models import StockItem, UnitOfMeasure

        unit = UnitOfMeasure.objects.filter(code="kg").first() or UnitOfMeasure.objects.first()
        item = StockItem.objects.create(restaurant=fiscal_restaurant, name="ფქვილი", base_unit=unit)
        lot = inv.receive_stock(
            item, Decimal("10"), unit, unit_cost=Decimal("2"), supplier_name="Supplier", reference="INV-7", by=user
        )
        doc = FiscalDocument.objects.get(kind="waybill_in")
        assert doc.status == "draft" and doc.stock_lot == lot and doc.payload["reference"] == "INV-7"
        assert doc.payload["goods"][0]["name"] == "ფქვილი"
        xml = build_waybill_xml(doc)
        assert xml.startswith(b"<WAYBILL>") and b"<W_NAME>" in xml and "ფქვილი".encode() in xml
        assert b"<BUYER_TIN>405123456</BUYER_TIN>" in xml

    def test_failed_provider_retries(self, order, user, monkeypatch, django_capture_on_commit_callbacks):
        from apps.fiscal.providers.null import NullProvider

        calls = {"n": 0}

        def flaky(self, document):
            calls["n"] += 1
            if calls["n"] == 1:
                return ProviderResult(ok=False, error="boom", retryable=True)
            return ProviderResult(ok=True, external_id="ok")

        monkeypatch.setattr(NullProvider, "issue_receipt", flaky)
        with django_capture_on_commit_callbacks(execute=True):
            p = ledger.record_payment(
                order.restaurant, method="card_terminal", amount=order.total, order=order, by=user
            )
        doc = FiscalDocument.objects.get(payment=p)
        assert doc.status == "failed" and doc.next_retry_at is not None and doc.attempts == 1
        from django.utils import timezone

        FiscalDocument.objects.filter(pk=doc.pk).update(next_retry_at=timezone.now())
        from apps.fiscal.tasks import retry_failed

        assert retry_failed() == 1
        doc.refresh_from_db()
        assert doc.status == "confirmed" and doc.attempts == 2

    def test_render_and_receipt_data(self, order, user, django_capture_on_commit_callbacks):
        from apps.printing.receipts import receipt_data

        with django_capture_on_commit_callbacks(execute=True):
            p = ledger.record_payment(
                order.restaurant, method="card_terminal", amount=order.total, order=order, by=user
            )
        doc = FiscalDocument.objects.get(payment=p)
        data = services.render(doc)
        assert data["number"] == "R-000001" and data["fiscal"] is False
        assert data["restaurant"]["legal_name"] == "შპს თიალი" and data["restaurant"]["tax_id_line"] == "ს/კ 405123456"
        assert data["vat_breakdown"][0]["rate"] == "18.00"
        plain = receipt_data(order, payment=p)
        assert plain["number"] == "R-000001" and plain["vat_breakdown"]


@pytest.mark.django_db(transaction=True)
class TestNumbering:
    def test_receipt_numbers_are_consecutive_under_threads(self, restaurant):
        import threading

        from django.db import connection

        out = []
        barrier = threading.Barrier(5)

        def worker():
            try:
                barrier.wait(timeout=5)
                out.append(ledger.next_number(restaurant.pk, "receipt"))
            finally:
                connection.close()

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(out) == [1, 2, 3, 4, 5]


class FakeResponse:
    def __init__(self, status_code, text):
        self.status_code, self.text = status_code, text


class FakeSession:
    def __init__(self, *responses):
        self.calls = []
        self.queue = list(responses)

    def post(self, url, data=None, headers=None, timeout=None):
        self.calls.append((url, data, headers))
        status, text = self.queue.pop(0)
        return FakeResponse(status, text)


class TestRsGe:
    def test_envelope_and_save_waybill(self):
        env = envelope("save_waybill", su="u", sp="p", waybill=b"<WAYBILL/>")
        assert (
            b'<save_waybill xmlns="http://tempuri.org/"><su>u</su><sp>p</sp><waybill>&lt;WAYBILL/&gt;</waybill></save_waybill>'
            in env
        )
        session = FakeSession(
            (
                200,
                "<soap:Envelope><soap:Body><save_waybillResponse><RESULT>12345</RESULT></save_waybillResponse></soap:Body></soap:Envelope>",
            )
        )
        client = RsGeWaybillClient("u", "p", session=session)
        result = client.save_waybill(b"<WAYBILL/>")
        assert result.ok and result.external_id == "12345"
        assert session.calls[0][2]["SOAPAction"] == "http://tempuri.org/save_waybill"
        session = FakeSession((200, "<x><RESULT>-1</RESULT></x>"))
        assert not RsGeWaybillClient("u", "p", session=session).save_waybill(b"<WAYBILL/>").ok

    def test_check_user(self):
        session = FakeSession((200, "<chek_service_userResult>true</chek_service_userResult>"))
        assert RsGeWaybillClient("u", "p", session=session).check_user().ok


@pytest.mark.django_db
class TestAdminAndApi:
    def _owner_admin(self, user, restaurant, staff_roles, create_staff_member):
        create_staff_member(user=user, restaurant=restaurant, role=next(r for r in staff_roles if r.name == "owner"))
        user.is_staff = True
        user.save()
        c = Client(HTTP_HOST=f"{restaurant.slug}.localhost")
        c.force_login(user)
        return c

    def test_settings_page_save_and_module_gating(self, user, fiscal_restaurant, staff_roles, create_staff_member):
        c = self._owner_admin(user, fiscal_restaurant, staff_roles, create_staff_member)
        html = c.get("/tenant-admin/fiscal/fiscalsettingspage/").content.decode()
        assert 'data-testid="fiscal-settings"' in html and "შპს თიალი" in html
        resp = c.post(
            "/tenant-admin/fiscal/fiscalsettingspage/save/",
            {
                "legal_name": "შპს ახალი",
                "tax_id": "405123456",
                "legal_address": "თბილისი",
                "vat_payer": "1",
                "vat_rate": "18.00",
                "prices_include_vat": "1",
                "provider": "rsge_stub",
                "receipt_prefix": "T",
                "receipt_footer": "მადლობა",
                "service_user": "svc",
                "service_password": "secret",
            },
        )
        assert resp.status_code == 302
        profile = FiscalProfile.objects.get(restaurant=fiscal_restaurant)
        assert profile.legal_name == "შპს ახალი" and profile.provider == "rsge_stub"
        assert profile.get_credentials() == {"service_user": "svc", "service_password": "secret"}
        assert "secret" not in c.get("/tenant-admin/fiscal/fiscalsettingspage/").content.decode()
        assert c.get("/tenant-admin/fiscal/fiscaldocument/").status_code == 200
        dash = c.get("/tenant-admin/").content.decode()
        assert 'data-testid="card-fiscal"' in dash and "/tenant-admin/fiscal/fiscalsettingspage/" in dash
        fiscal_restaurant.fiscal_enabled = False
        fiscal_restaurant.save(update_fields=["fiscal_enabled"])
        assert c.get("/tenant-admin/fiscal/fiscalsettingspage/").status_code == 403

    def test_waiter_403_and_xml_download(self, user, waiter_staff, fiscal_restaurant, staff_roles, create_staff_member):
        waiter = Client(HTTP_HOST=f"{fiscal_restaurant.slug}.localhost")
        waiter_staff.user.is_staff = True
        waiter_staff.user.save()
        waiter.force_login(waiter_staff.user)
        assert waiter.get("/tenant-admin/fiscal/fiscalsettingspage/").status_code == 403
        doc = FiscalDocument.objects.create(
            restaurant=fiscal_restaurant,
            kind="waybill_in",
            status="draft",
            fiscal_number="WB-000001",
            payload={"goods": [], "reference": "X"},
        )
        c = self._owner_admin(user, fiscal_restaurant, staff_roles, create_staff_member)
        resp = c.get(f"/tenant-admin/fiscal/fiscaldocument/{doc.pk}/xml/")
        assert resp.status_code == 200 and resp["Content-Type"] == "application/xml"
        assert 'filename="waybill-WB-000001.xml"' in resp["Content-Disposition"]

    def test_receipt_api(self, authenticated_owner_client, order, user, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            p = ledger.record_payment(
                order.restaurant, method="card_terminal", amount=order.total, order=order, by=user
            )
        authenticated_owner_client.defaults["HTTP_X_RESTAURANT"] = order.restaurant.slug
        resp = authenticated_owner_client.get(f"/api/v1/dashboard/fiscal/payments/{p.id}/receipt/")
        assert resp.status_code == 200, resp.content
        data = resp.json().get("data") or resp.json()
        assert data["number"] == "R-000001" and data["status"] == "confirmed" and data["vat"]["rate"] == "18.00"
        resp = authenticated_owner_client.get("/api/v1/dashboard/fiscal/documents/?kind=receipt")
        assert resp.status_code == 200 and resp.json()["count"] == 1

    def test_settings_api_ignores_tax_rate(self, authenticated_owner_client, restaurant):
        authenticated_owner_client.defaults["HTTP_X_RESTAURANT"] = restaurant.slug
        resp = authenticated_owner_client.patch("/api/v1/dashboard/settings/", {"tax_rate": "25"}, format="json")
        assert resp.status_code == 200
        restaurant.refresh_from_db()
        assert restaurant.tax_rate == Decimal("0")
