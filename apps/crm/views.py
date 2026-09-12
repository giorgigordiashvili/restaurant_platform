"""Dashboard API (POS / SPA): customer lookup by phone, list, consent; segments; campaigns. Public: unsubscribe."""

from __future__ import annotations

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _

from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.utils import extend_schema

from apps.core.middleware.tenant import require_restaurant
from apps.core.permissions import HasStaffPermission, IsTenantStaff, ModuleRequired
from apps.crm import services
from apps.crm.models import Campaign, Customer, Segment
from apps.crm.serializers import (
    CampaignSerializer,
    ConsentSerializer,
    CustomerSerializer,
    SegmentSerializer,
    SummarySerializer,
)
from apps.notifications.providers import normalize_phone

TAG = "Dashboard - CRM"
PERMS = [IsAuthenticated, IsTenantStaff, HasStaffPermission, ModuleRequired("crm")]


@extend_schema(tags=[TAG])
class CustomerListView(generics.ListAPIView):
    """?q= searches name / phone / email; ?opt_in=1; ?tag=."""

    permission_classes = PERMS
    required_permission = ("crm", "read")
    serializer_class = CustomerSerializer

    @require_restaurant
    def get_queryset(self):
        qs = Customer.objects.filter(restaurant=self.request.restaurant)
        q = self.request.query_params.get("q", "").strip()
        if q:
            from django.db.models import Q

            qs = qs.filter(Q(name__icontains=q) | Q(phone__icontains=q) | Q(email__icontains=q))
        if self.request.query_params.get("opt_in") in ("1", "true"):
            qs = qs.filter(marketing_opt_in=True)
        tag = self.request.query_params.get("tag")
        if tag:
            qs = qs.filter(tags__contains=[tag])
        return qs


@extend_schema(tags=[TAG], responses=CustomerSerializer)
class CustomerLookupView(APIView):
    """The POS badge: who is this phone number? 404 when unknown (orders:read is enough)."""

    permission_classes = PERMS
    required_permission = ("orders", "read")

    @require_restaurant
    def get(self, request):
        phone = normalize_phone(request.query_params.get("phone", ""))
        if not phone:
            return Response({"detail": "phone required"}, status=status.HTTP_400_BAD_REQUEST)
        c = Customer.objects.filter(restaurant=request.restaurant, phone=phone).first()
        if c is None:
            return Response({"detail": "unknown"}, status=status.HTTP_404_NOT_FOUND)
        return Response(CustomerSerializer(c).data)


@extend_schema(tags=[TAG], request=CustomerSerializer, responses=CustomerSerializer)
class CustomerDetailView(APIView):
    permission_classes = PERMS
    required_permission = ("crm", "read")

    @require_restaurant
    def get(self, request, customer_id):
        c = get_object_or_404(Customer, pk=customer_id, restaurant=request.restaurant)
        return Response(CustomerSerializer(c).data)

    @require_restaurant
    def patch(self, request, customer_id):
        from apps.core.permissions import staff_can

        if not staff_can(request, "crm", "update"):
            return Response({"detail": "crm:update required"}, status=403)
        c = get_object_or_404(Customer, pk=customer_id, restaurant=request.restaurant)
        ser = CustomerSerializer(c, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(CustomerSerializer(c).data)


@extend_schema(tags=[TAG], request=ConsentSerializer, responses=CustomerSerializer)
class CustomerConsentView(APIView):
    """Staff record a guest's spoken consent (or withdrawal) at the counter."""

    permission_classes = PERMS
    required_permission = ("crm", "update")

    @require_restaurant
    def post(self, request, customer_id):
        c = get_object_or_404(Customer, pk=customer_id, restaurant=request.restaurant)
        ser = ConsentSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        services.set_consent(c, ser.validated_data["marketing_opt_in"], source="staff")
        return Response(CustomerSerializer(c).data)


@extend_schema(tags=[TAG], responses=SummarySerializer)
class SummaryView(APIView):
    permission_classes = PERMS
    required_permission = ("crm", "read")

    @require_restaurant
    def get(self, request):
        return Response(SummarySerializer(services.summary(request.restaurant)).data)


@extend_schema(tags=[TAG], responses=SegmentSerializer(many=True))
class SegmentListView(APIView):
    permission_classes = PERMS
    required_permission = ("crm", "read")

    @require_restaurant
    def get(self, request):
        rows = []
        for s in Segment.objects.filter(restaurant=request.restaurant, is_active=True):
            s.count = services.segment_count(s)
            rows.append(s)
        return Response(SegmentSerializer(rows, many=True).data)


@extend_schema(tags=[TAG])
class CampaignListView(generics.ListAPIView):
    permission_classes = PERMS
    required_permission = ("crm", "read")
    serializer_class = CampaignSerializer

    @require_restaurant
    def get_queryset(self):
        return Campaign.objects.filter(restaurant=self.request.restaurant).select_related("segment")


@extend_schema(exclude=True)
class UnsubscribeView(APIView):
    """Signed link in every marketing message; GET shows a confirmation, POST (or ?confirm=1) opts out."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def _page(self, text: str, status_code=200):
        html = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>AiMenu</title><style>body{{font-family:system-ui,sans-serif;max-width:480px;margin:60px auto;padding:0 20px;color:#111}}
button{{padding:10px 18px;border-radius:10px;border:0;background:#111;color:#fff;font-size:15px}}</style></head>
<body>{text}</body></html>"""
        return HttpResponse(html, status=status_code)

    def get(self, request, token):
        c = services.customer_from_token(token)
        if c is None:
            return self._page(f"<p>{_('This link is not valid.')}</p>", 404)
        if request.query_params.get("confirm") == "1":
            services.set_consent(c, False, source="link")
            return self._page(
                f"<p>{_('You will no longer receive messages from %(restaurant)s.') % {'restaurant': c.restaurant.name}}</p>"
            )
        return self._page(
            f"<p>{_('Stop receiving messages from %(restaurant)s?') % {'restaurant': c.restaurant.name}}</p>"
            f"<form method=\"post\"><button type=\"submit\">{_('Unsubscribe')}</button></form>"
        )

    def post(self, request, token):
        c = services.customer_from_token(token)
        if c is None:
            return self._page(f"<p>{_('This link is not valid.')}</p>", 404)
        services.set_consent(c, False, source="link")
        return self._page(
            f"<p>{_('You will no longer receive messages from %(restaurant)s.') % {'restaurant': c.restaurant.name}}</p>"
        )
