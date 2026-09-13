"""Dashboard API (POS charge / settle / lookup, admin CRUD) and the public statement page."""

from __future__ import annotations

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _

from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from drf_spectacular.utils import OpenApiParameter, extend_schema

from apps.core.middleware.tenant import require_restaurant
from apps.core.permissions import HasStaffPermission, IsTenantStaff, ModuleRequired, staff_can
from apps.houseaccounts import services
from apps.houseaccounts.models import HouseAccount, HouseAccountStatement
from apps.houseaccounts.serializers import (
    AdjustSerializer,
    ChargeSerializer,
    EntrySerializer,
    GenerateStatementSerializer,
    HouseAccountSerializer,
    SettleSerializer,
    StatementSerializer,
    StatusSerializer,
    SummarySerializer,
)
from apps.orders.models import Order
from apps.tables.models import TableSession

TAG = "Dashboard - House accounts"
PERMS = [IsAuthenticated, IsTenantStaff, HasStaffPermission, ModuleRequired("house_accounts")]


def _err(exc, http=status.HTTP_409_CONFLICT):
    return Response({"success": False, "error": {"code": exc.code, "message": exc.message, **exc.extra}}, status=http)


def _ledger_err(exc):
    return Response(
        {
            "success": False,
            "error": {"code": getattr(exc, "code", "ledger_error"), "message": str(exc), **getattr(exc, "extra", {})},
        },
        status=409,
    )


@extend_schema(tags=[TAG], responses=SummarySerializer)
class SummaryView(APIView):
    permission_classes = PERMS
    required_permission = ("cash", "read")

    @require_restaurant
    def get(self, request):
        return Response(SummarySerializer(services.summary(request.restaurant)).data)


@extend_schema(tags=[TAG])
class AccountListView(generics.ListCreateAPIView):
    permission_classes = PERMS
    required_permission = ("cash", "read")
    serializer_class = HouseAccountSerializer
    pagination_class = None

    @require_restaurant
    def get_queryset(self):
        qs = HouseAccount.objects.filter(restaurant=self.request.restaurant).select_related("customer")
        q = self.request.query_params.get("q", "").strip()
        if q:
            from django.db.models import Q

            qs = qs.filter(Q(name__icontains=q) | Q(company__icontains=q) | Q(phone__icontains=q))
        st = self.request.query_params.get("status", "active")
        if st and st != "all":
            qs = qs.filter(status=st)
        return qs[:200]

    def perform_create(self, serializer):
        if not staff_can(self.request, "cash", "update"):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("cash:update needed.")
        serializer.save(restaurant=self.request.restaurant)


@extend_schema(tags=[TAG])
class AccountDetailView(generics.RetrieveUpdateAPIView):
    permission_classes = PERMS
    required_permission = ("cash", "read")
    serializer_class = HouseAccountSerializer
    lookup_url_kwarg = "account_id"

    @require_restaurant
    def get_queryset(self):
        return HouseAccount.objects.filter(restaurant=self.request.restaurant)

    def perform_update(self, serializer):
        if not staff_can(self.request, "cash", "update"):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("cash:update needed.")
        serializer.save()


@extend_schema(tags=[TAG], parameters=[OpenApiParameter("phone", str)], responses=HouseAccountSerializer)
class LookupView(APIView):
    """POS: is this phone number on an account?"""

    permission_classes = PERMS
    required_permission = ("cash", "read")

    @require_restaurant
    def get(self, request):
        account = services.lookup(request.restaurant, phone=request.query_params.get("phone", ""))
        if account is None:
            return Response({"detail": "unknown"}, status=status.HTTP_404_NOT_FOUND)
        return Response(HouseAccountSerializer(account).data)


class _AccountView(APIView):
    permission_classes = PERMS
    required_permission = ("cash", "create")

    def _account(self, request, account_id) -> HouseAccount:
        return get_object_or_404(HouseAccount, pk=account_id, restaurant=request.restaurant)


@extend_schema(tags=[TAG], request=ChargeSerializer, responses={200: dict})
class ChargeView(_AccountView):
    @require_restaurant
    def post(self, request, account_id):
        account = self._account(request, account_id)
        s = ChargeSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data
        kwargs = {}
        if v.get("order_id"):
            kwargs["order"] = get_object_or_404(Order, pk=v["order_id"], restaurant=request.restaurant)
        elif v.get("order_ids"):
            kwargs["orders"] = list(Order.objects.filter(pk__in=v["order_ids"], restaurant=request.restaurant))
        else:
            kwargs["session"] = get_object_or_404(
                TableSession, pk=v["session_id"], table__restaurant=request.restaurant
            )
        try:
            payment, entry = services.charge(
                account, v["amount"], by=request.user, signed_by=v["signed_by"], note=v["note"], **kwargs
            )
        except services.HouseAccountError as exc:
            return _err(exc)
        except Exception as exc:  # LedgerError
            return _ledger_err(exc)
        from apps.payments import services as ledger
        from apps.payments.serializers import PaymentSerializer

        account.refresh_from_db()
        target_orders = kwargs.get("orders") or (
            [kwargs["order"]] if kwargs.get("order") else ledger.unpaid_orders(kwargs["session"])
        )
        remaining = sum((ledger.balance(o) for o in target_orders), 0)
        return Response(
            {
                "payment": PaymentSerializer(payment).data,
                "receipt_number": payment.receipt_number,
                "balance": str(remaining),
                "account": HouseAccountSerializer(account).data,
                "change": "0.00",
                "paid_order_numbers": [o.order_number for o in target_orders if ledger.is_paid(o)],
            }
        )


@extend_schema(tags=[TAG], request=SettleSerializer, responses={200: dict})
class SettleView(_AccountView):
    @require_restaurant
    def post(self, request, account_id):
        account = self._account(request, account_id)
        s = SettleSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data
        try:
            payment, entry = services.settle(
                account, v["amount"], method=v["method"], by=request.user, tendered=v.get("tendered"), note=v["note"]
            )
        except services.HouseAccountError as exc:
            return _err(exc)
        except Exception as exc:
            return _ledger_err(exc)
        from apps.payments.serializers import PaymentSerializer

        account.refresh_from_db()
        return Response(
            {
                "payment": PaymentSerializer(payment).data,
                "receipt_number": payment.receipt_number,
                "account": HouseAccountSerializer(account).data,
                "change": str(payment.change_given),
            }
        )


@extend_schema(tags=[TAG], request=AdjustSerializer, responses=EntrySerializer)
class AdjustView(_AccountView):
    required_permission = ("cash", "update")

    @require_restaurant
    def post(self, request, account_id):
        s = AdjustSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        entry = services.adjust(
            self._account(request, account_id),
            s.validated_data["amount"],
            by=request.user,
            note=s.validated_data["note"],
            writeoff=s.validated_data["writeoff"],
        )
        return Response(EntrySerializer(entry).data)


@extend_schema(tags=[TAG], request=StatusSerializer, responses=HouseAccountSerializer)
class StatusView(_AccountView):
    required_permission = ("cash", "update")

    @require_restaurant
    def post(self, request, account_id):
        s = StatusSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            account = services.set_status(
                self._account(request, account_id), s.validated_data["status"], by=request.user
            )
        except services.HouseAccountError as exc:
            return _err(exc)
        return Response(HouseAccountSerializer(account).data)


@extend_schema(tags=[TAG], responses=EntrySerializer(many=True))
class EntriesView(_AccountView):
    required_permission = ("cash", "read")

    @require_restaurant
    def get(self, request, account_id):
        return Response(
            EntrySerializer(
                self._account(request, account_id).entries.select_related("order", "payment")[:200], many=True
            ).data
        )


@extend_schema(tags=[TAG])
class StatementsView(_AccountView):
    required_permission = ("cash", "read")

    @extend_schema(responses=StatementSerializer(many=True))
    @require_restaurant
    def get(self, request, account_id):
        return Response(StatementSerializer(self._account(request, account_id).statements.all()[:24], many=True).data)

    @extend_schema(request=GenerateStatementSerializer, responses=StatementSerializer)
    @require_restaurant
    def post(self, request, account_id):
        if not staff_can(request, "cash", "update"):
            return Response({"detail": "cash:update needed."}, status=status.HTTP_403_FORBIDDEN)
        account = self._account(request, account_id)
        s = GenerateStatementSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        v = s.validated_data
        start, end = (
            (v["period_start"], v["period_end"])
            if v.get("period_start") and v.get("period_end")
            else services.previous_month()
        )
        stmt = services.build_statement(account, start, end)
        if v["send"]:
            services.send_statement(stmt, by=request.user, force=True)
            stmt.refresh_from_db()
        return Response(StatementSerializer(stmt).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=[TAG], request=None, responses=StatementSerializer)
class StatementSendView(_AccountView):
    required_permission = ("cash", "update")

    @require_restaurant
    def post(self, request, account_id, statement_id):
        account = self._account(request, account_id)
        stmt = get_object_or_404(HouseAccountStatement, pk=statement_id, account=account)
        if not services.send_statement(stmt, by=request.user, force=True):
            return Response({"success": False, "error": {"code": "no_contact"}}, status=409)
        stmt.refresh_from_db()
        return Response(StatementSerializer(stmt).data)


# ── public statement page ──────────────────────────────────────────────────


class PublicThrottle(AnonRateThrottle):
    scope = "house_account_public"
    rate = "30/min"


@extend_schema(tags=["House accounts"], responses={200: dict})
class PublicStatementView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [PublicThrottle]

    def get(self, request, token):
        stmt = get_object_or_404(HouseAccountStatement.objects.select_related("account__restaurant"), token=token)
        account = stmt.account
        r = account.restaurant
        legal = ""
        try:
            profile = getattr(r, "fiscal_profile", None)
            if profile is not None and getattr(profile, "legal_name", ""):
                legal = f"{profile.legal_name}{' · ' + profile.tax_id if getattr(profile, 'tax_id', '') else ''}<br>{getattr(profile, 'legal_address', '')}"
        except Exception:  # noqa: BLE001
            legal = ""
        if request.query_params.get("format") == "json" or "text/html" not in request.headers.get("Accept", ""):
            return Response(
                {
                    "success": True,
                    "data": StatementSerializer(stmt).data
                    | {"lines": stmt.lines, "account": account.name, "restaurant": r.name},
                }
            )
        rows = "".join(
            f"<tr><td>{l['date']}</td><td>{l['label']}{' · ' + l['order'] if l['order'] else ''}{' · ' + l['signed_by'] if l['signed_by'] else ''}</td><td style='text-align:right'>{l['amount']}</td><td style='text-align:right'>{l['balance_after']}</td></tr>"
            for l in stmt.lines
        )
        html = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{r.name} · {_('Statement')}</title><style>body{{font-family:system-ui,sans-serif;max-width:720px;margin:40px auto;padding:0 20px;color:#111}}
table{{width:100%;border-collapse:collapse;font-size:14px}}td,th{{padding:6px 4px;border-bottom:1px solid #eee}}th{{text-align:left;color:#666;font-size:12px;text-transform:uppercase}}
.tot{{display:flex;justify-content:space-between;font-size:15px;margin:4px 0}} @media print{{button{{display:none}}}}</style></head><body>
<h2>{r.name}</h2><p style="color:#666;font-size:13px">{legal or r.full_address}</p>
<h3>{_('Statement')} · {account.name}{' · ' + account.company if account.company else ''}</h3><p>{stmt.period_start:%d.%m.%Y} – {stmt.period_end:%d.%m.%Y}</p>
<div class="tot"><span>{_('Opening balance')}</span><b>{stmt.opening} {r.default_currency}</b></div>
<div class="tot"><span>{_('Charges')}</span><b>{stmt.charges}</b></div><div class="tot"><span>{_('Payments')}</span><b>−{stmt.payments}</b></div>
<div class="tot"><span>{_('Adjustments')}</span><b>{stmt.adjustments}</b></div><div class="tot" style="font-size:18px"><span>{_('Balance due')}</span><b>{stmt.closing} {r.default_currency}</b></div>
<table><thead><tr><th>{_('Date')}</th><th>{_('Entry')}</th><th style="text-align:right">{_('Amount')}</th><th style="text-align:right">{_('Balance')}</th></tr></thead><tbody>{rows}</tbody></table>
<p><button onclick="window.print()">{_('Print')}</button></p></body></html>"""
        return HttpResponse(html)
