"""Terminal transactions: manual confirm, BOG / TBC pay-by-link with FakeSession, bridge, timeouts, refunds, reconciliation, API + admin."""

import json
from decimal import Decimal

from rest_framework.test import APIClient

import pytest

from apps.notifications.models import Notification, OutboundMessage
from apps.payments import services as ledger
from apps.payments.models import Payment
from apps.terminals import services
from apps.terminals.models import PaymentTerminal, TerminalTransaction
from apps.terminals.providers.base import Result
from tests.terminals.conftest import FakeSession, make_order

D = "/api/v1/dashboard/terminals/"


@pytest.mark.django_db
class TestManual:
    def test_start_confirm_books_payment(self, terminals, manual_terminal, paid_order, user, open_shift):
        tx = services.start_sale(terminals, manual_terminal, "30.00", order=paid_order, tip="2.00", by=user)
        assert tx.status == "awaiting_confirm" and tx.total == Decimal("32.00")
        with pytest.raises(services.TerminalServiceError) as exc:
            services.start_sale(terminals, manual_terminal, "30.00", order=paid_order, by=user)
        assert exc.value.code == "terminal_busy"
        services.confirm_manual(tx, by=user, card_mask="**** 1234", auth_code="A1")
        tx.refresh_from_db()
        assert tx.status == "approved" and tx.payment_id and tx.payment.payment_method == "card_terminal"
        assert tx.payment.terminal_id == manual_terminal.pk and tx.payment.tip_amount == Decimal("2.00")
        assert ledger.is_paid(paid_order)
        # idempotent: confirming again changes nothing
        services.apply_result(tx, Result(status="approved"))
        assert Payment.objects.filter(external_payment_id=f"tx:{tx.pk}").count() == 1

    def test_overpay_and_decline(self, terminals, manual_terminal, paid_order, user, open_shift):
        with pytest.raises(services.TerminalServiceError) as exc:
            services.start_sale(terminals, manual_terminal, "31.00", order=paid_order, by=user)
        assert exc.value.code == "overpay"
        tx = services.start_sale(terminals, manual_terminal, "10.00", order=paid_order, by=user)
        services.decline_manual(tx, by=user, reason="Card refused")
        tx.refresh_from_db()
        assert tx.status == "declined" and tx.payment_id is None
        assert Notification.objects.filter(title__startswith="Card payment declined").exists()
        assert not ledger.is_paid(paid_order)

    def test_session_target_and_timeout(self, terminals, manual_terminal, paid_order, user, open_shift, table):
        from apps.tables.models import TableSession

        session = TableSession.objects.create(table=table, status="active")
        paid_order.table_session = session
        paid_order.save()
        tx = services.start_sale(terminals, manual_terminal, "30.00", session=session, by=user)
        assert tx.session_id == session.pk and tx.order_id is None
        from django.utils import timezone

        tx.expires_at = timezone.now() - timezone.timedelta(seconds=1)
        tx.save()
        assert services.expire_stale() == 1
        tx.refresh_from_db()
        assert tx.status == "timeout"

    def test_refund_manual(self, terminals, manual_terminal, paid_order, user, open_shift):
        tx = services.start_sale(terminals, manual_terminal, "30.00", order=paid_order, by=user)
        services.confirm_manual(tx, by=user)
        tx.refresh_from_db()
        rtx, refund = services.start_refund(tx.payment, "10.00", by=user, reason="wrong dish")
        assert rtx.status == "approved" and refund is not None and refund.amount == Decimal("10.00")
        assert rtx.refund_of_id == tx.pk


BOG_CREATE = (200, {"id": "bog-77", "_links": {"redirect": {"href": "https://pay.bog.ge/x"}}})
BOG_TOKEN = (200, {"access_token": "t", "expires_in": 3600})


@pytest.mark.django_db
class TestBogLink:
    def _session(self):
        return FakeSession(BOG_TOKEN, BOG_CREATE)

    def test_start_sends_link_and_poll_approves(
        self, terminals, bog_terminal, paid_order, user, open_shift, monkeypatch
    ):
        fake = FakeSession(
            BOG_TOKEN,
            BOG_CREATE,
            BOG_TOKEN,  # a fresh provider instance re-authenticates
            (
                200,
                {
                    "order_id": "bog-77",
                    "order_status": {"key": "completed"},
                    "payment_detail": {"card_type": "visa", "pan": "411111******1111", "auth_code": "Z9"},
                },
            ),
        )
        tx = services.start_sale(
            terminals, bog_terminal, "30.00", order=paid_order, by=user, send_to="+995555111222", session_http=fake
        )
        assert tx.status == "sent" and tx.external_id == "bog-77" and tx.pay_url.startswith("https://pay.bog.ge")
        create = fake.calls[1]
        assert (
            create["json"]["external_order_id"] == str(tx.pk)
            and create["json"]["purchase_units"]["total_amount"] == 30.0
        )
        assert create["json"]["callback_url"] == "https://api.test/api/v1/terminals/bog/callback/"
        assert OutboundMessage.objects.filter(kind="pay_link", to="+995555111222", body__contains="pay.bog.ge").exists()
        tx = services.poll(tx, session_http=fake)
        assert tx.status == "approved" and tx.card_mask == "visa 1111" and tx.auth_code == "Z9"
        assert tx.payment.payment_method == "online_bog" and ledger.is_paid(paid_order)

    def test_callback_bad_signature_and_rejected(
        self, terminals, bog_terminal, paid_order, user, open_shift, monkeypatch
    ):
        tx = services.start_sale(
            terminals, bog_terminal, "30.00", order=paid_order, by=user, session_http=self._session()
        )
        monkeypatch.setattr("apps.payments.bog.signatures.verify_signature", lambda raw, sig: sig == "good")
        body = json.dumps(
            {
                "event": "order_payment",
                "body": {
                    "order_id": "bog-77",
                    "order_status": {"key": "rejected"},
                    "reject_reason": "insufficient funds",
                },
            }
        )
        res = APIClient().generic(
            "POST",
            "/api/v1/terminals/bog/callback/",
            body,
            content_type="application/json",
            HTTP_CALLBACK_SIGNATURE="bad",
        )
        assert res.status_code == 401
        res = APIClient().generic(
            "POST",
            "/api/v1/terminals/bog/callback/",
            body,
            content_type="application/json",
            HTTP_CALLBACK_SIGNATURE="good",
        )
        assert res.status_code == 200
        tx.refresh_from_db()
        assert tx.status == "declined" and "insufficient" in tx.error

    def test_provider_failure_marks_failed(self, terminals, bog_terminal, paid_order, user, open_shift):
        fake = FakeSession(BOG_TOKEN, (400, {"message": "bad merchant"}))
        with pytest.raises(services.TerminalServiceError) as exc:
            services.start_sale(terminals, bog_terminal, "30.00", order=paid_order, by=user, session_http=fake)
        assert exc.value.code == "provider_failed"
        assert TerminalTransaction.objects.get().status == "failed"


TBC_TOKEN = (200, {"access_token": "tt", "expires_in": 86400})
TBC_CREATE = (
    200,
    {
        "payId": "tp-1",
        "status": "Created",
        "links": [{"uri": "https://tpay.tbcbank.ge/p/1", "method": "GET", "rel": "approval_url"}],
    },
)


@pytest.mark.django_db
class TestTbc:
    def test_start_callback_poll(self, terminals, tbc_terminal, paid_order, user, open_shift, monkeypatch):
        fake = FakeSession(
            TBC_TOKEN,
            TBC_CREATE,
            (200, {"payId": "tp-1", "status": "Succeeded", "cardMask": "5*** 4444", "approvalCode": "OK1"}),
        )
        tx = services.start_sale(terminals, tbc_terminal, "30.00", order=paid_order, by=user, session_http=fake)
        assert tx.status == "sent" and tx.external_id == "tp-1" and "tpay" in tx.pay_url
        token, create = fake.calls[:2]
        assert token["headers"]["apikey"] == "ak" and token["data"]["client_Id"] == "cid"
        assert create["json"]["amount"]["total"] == 30.0 and create["json"]["extra"] == str(tx.pk)
        assert create["headers"]["Authorization"] == "Bearer tt"
        # callback body is never trusted: it triggers a poll
        monkeypatch.setattr(
            "apps.terminals.providers.registry.get_provider", lambda t, session=None: _tbc_with(t, fake)
        )
        res = APIClient().post("/api/v1/terminals/tbc/callback/", {"PaymentId": "tp-1"}, format="json")
        assert res.status_code == 200
        tx.refresh_from_db()
        assert tx.status == "approved" and tx.card_mask == "5*** 4444" and tx.payment.payment_method == "online_tbc"

    def test_expired_link(self, terminals, tbc_terminal, paid_order, user, open_shift, monkeypatch):
        fake = FakeSession(TBC_TOKEN, TBC_CREATE)
        tx = services.start_sale(terminals, tbc_terminal, "30.00", order=paid_order, by=user, session_http=fake)
        fake.queue.append((200, {"payId": "tp-1", "status": "Expired"}))
        monkeypatch.setattr(
            "apps.terminals.providers.registry.get_provider", lambda t, session=None: _tbc_with(t, fake)
        )
        from django.utils import timezone

        TerminalTransaction.objects.filter(pk=tx.pk).update(expires_at=timezone.now() - timezone.timedelta(seconds=5))
        assert services.expire_stale() == 1
        tx.refresh_from_db()
        assert tx.status == "timeout"


def _tbc_with(terminal, fake):
    from apps.terminals.providers.tbc_tpay import TbcTpayProvider

    return TbcTpayProvider(terminal, session=fake)


@pytest.mark.django_db
class TestBridge:
    def test_claim_result_and_key(self, terminals, bridge_terminal, paid_order, user, open_shift):
        tx = services.start_sale(terminals, bridge_terminal, "30.00", order=paid_order, by=user)
        assert tx.status == "pending"
        c = APIClient()
        assert c.get("/api/v1/terminal-bridge/jobs/next/", HTTP_X_BRIDGE_KEY="nope").status_code == 401
        res = c.get("/api/v1/terminal-bridge/jobs/next/", HTTP_X_BRIDGE_KEY=bridge_terminal.bridge_key)
        assert (
            res.status_code == 200
            and res.data["id"] == str(tx.pk)
            and res.data["amount"] == "30.00"
            and res.data["protocol"] == "bog"
        )
        assert (
            c.get("/api/v1/terminal-bridge/jobs/next/", HTTP_X_BRIDGE_KEY=bridge_terminal.bridge_key).status_code == 204
        )
        res = c.post(
            f"/api/v1/terminal-bridge/jobs/{tx.pk}/result/",
            {"status": "approved", "card_mask": "**** 9", "rrn": "R1"},
            format="json",
            HTTP_X_BRIDGE_KEY=bridge_terminal.bridge_key,
        )
        assert res.status_code == 200 and res.data["status"] == "approved"
        tx.refresh_from_db()
        assert tx.payment_id and tx.rrn == "R1" and ledger.is_paid(paid_order)
        bridge_terminal.refresh_from_db()
        assert bridge_terminal.is_online
        # refund goes through the bridge as a job
        rtx, refund = services.start_refund(tx.payment, "5.00", by=user)
        assert refund is None and rtx.status == "sent"
        res = c.post(
            f"/api/v1/terminal-bridge/jobs/{rtx.pk}/result/",
            {"status": "approved"},
            format="json",
            HTTP_X_BRIDGE_KEY=bridge_terminal.bridge_key,
        )
        rtx.refresh_from_db()
        assert rtx.status == "approved" and rtx.response.get("refund_id")
        assert tx.payment.refunds.count() == 1


@pytest.mark.django_db
class TestApiAndAdmin:
    def test_dashboard_flow(self, terminals, manual_terminal, paid_order, owner_api, open_shift):
        rows = owner_api.get(D + "terminals/").data
        assert rows[0]["name"] == "Till 1" and rows[0]["configured"] is True
        res = owner_api.post(
            D + "transactions/",
            {"terminal_id": str(manual_terminal.pk), "amount": "30.00", "order_id": str(paid_order.pk)},
            format="json",
        )
        assert res.status_code == 201, res.content
        tx_id = res.data["id"]
        assert owner_api.get(D + f"transactions/{tx_id}/").data["status"] == "awaiting_confirm"
        res = owner_api.post(
            D + "transactions/",
            {"terminal_id": str(manual_terminal.pk), "amount": "30.00", "order_id": str(paid_order.pk)},
            format="json",
        )
        assert res.status_code == 409 and res.data["error"]["code"] == "terminal_busy"
        res = owner_api.post(D + f"transactions/{tx_id}/confirm/", {"card_mask": "**** 1"}, format="json")
        assert res.status_code == 200 and res.data["status"] == "approved" and res.data["receipt_number"]
        assert owner_api.get(D + "summary/").data["terminals"] == 1
        pid = res.data["payment_id"]
        res = owner_api.post(D + f"payments/{pid}/refund/", {"amount": "5.00"}, format="json")
        assert res.status_code == 201 and res.data["kind"] == "refund"
        assert len(owner_api.get(D + "transactions/?status=approved").data) == 2

    def test_module_off(self, restaurant, owner_api):
        assert owner_api.get(D + "terminals/").status_code == 403

    def test_admin_pages(self, terminals, manual_terminal, paid_order, owner_admin, user, open_shift):
        assert (
            "How card payments reach the ledger"
            in owner_admin.get("/tenant-admin/terminals/paymentterminal/").content.decode()
        )
        res = owner_admin.post(
            "/tenant-admin/terminals/paymentterminal/add/",
            {
                "name": "ECR till",
                "provider": "ecr_bridge",
                "ecr_protocol": "bog",
                "terminal_id": "T1",
                "is_active": "on",
                "auto_receipt": "on",
                "timeout_seconds": "120",
                "device": "tcp://10.0.0.5:8000",
            },
        )
        assert res.status_code == 302, res.content.decode()[:400]
        t = PaymentTerminal.objects.get(name="ECR till")
        assert t.connection == {"device": "tcp://10.0.0.5:8000"}
        html = owner_admin.get(f"/tenant-admin/terminals/paymentterminal/{t.pk}/change/").content.decode()
        assert t.bridge_key in html
        tx = services.start_sale(terminals, manual_terminal, "30.00", order=paid_order, by=user)
        assert owner_admin.get(f"/tenant-admin/terminals/terminaltransaction/{tx.pk}/confirm/").status_code == 302
        tx.refresh_from_db()
        assert tx.status == "approved"
        html = owner_admin.get("/tenant-admin/terminals/terminalreconciliation/").content.decode()
        assert "Till 1" in html and "30.00" in html

    def test_pay_page(self, terminals, manual_terminal, paid_order, user, open_shift):
        tx = services.start_sale(terminals, manual_terminal, "30.00", order=paid_order, by=user)
        html = APIClient().get(f"/api/v1/terminals/pay/{tx.pk}/").content.decode()
        assert "30.00" in html
        assert APIClient().get("/api/v1/terminals/pay/00000000-0000-0000-0000-000000000000/").status_code == 404
