"""
Dashboard (tenant-scoped) venue endpoints: /api/v1/dashboard/venue/.

Every view is scoped to ``request.restaurant`` (X-Restaurant header) and
gated by role permissions on the "tables" resource: read for viewing,
create for managing the venue, delete for leaving. Waiters have
tables:read/update only, so they can look but not act.
"""

from django.db.models import Q

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.utils import extend_schema

from apps.core.middleware.tenant import require_restaurant
from apps.core.permissions import HasStaffPermission, IsTenantManager, staff_can
from apps.tables.models import Table

from . import services
from .models import VenueSection, VenueShareRequest, VenueTable
from .serializers import (
    SyncSummarySerializer,
    VenueCardSerializer,
    VenueLeaveSerializer,
    VenueMemberSerializer,
    VenueSectionDashboardSerializer,
    VenueSectionWriteSerializer,
    VenueShareRequestAcceptSerializer,
    VenueShareRequestCreateSerializer,
    VenueShareRequestSerializer,
    VenueStateResponseSerializer,
    VenueTableDashboardSerializer,
    VenueTableWriteSerializer,
)

VENUE_READ = ("tables", "read")
VENUE_MANAGE = ("tables", "create")
VENUE_LEAVE = ("tables", "delete")

TAG = "Dashboard - Venue"


def error_response(exc: services.VenueError):
    body = {"success": False, "error": {"code": exc.code, "message": exc.message}}
    if exc.field_name:
        body["error"]["field"] = exc.field_name
    return Response(body, status=exc.status_code)


def build_state(request):
    restaurant = request.restaurant
    membership = services.get_membership(restaurant)
    venue = membership.venue if membership and membership.venue.is_active else None

    members = []
    if venue:
        for m in venue.active_memberships():
            members.append(
                {
                    "slug": m.restaurant.slug,
                    "name": m.restaurant.name,
                    "logo": m.restaurant.logo,
                    "display_order": m.display_order,
                    "is_layout_seed": m.is_layout_seed,
                    "is_me": m.restaurant_id == restaurant.pk,
                }
            )

    pending = VenueShareRequest.objects.filter(status=VenueShareRequest.STATUS_PENDING).select_related(
        "from_restaurant", "to_restaurant"
    )
    ctx = {"restaurant": restaurant, "request": request}
    incoming = VenueShareRequestSerializer(pending.filter(to_restaurant=restaurant), many=True, context=ctx).data
    outgoing = VenueShareRequestSerializer(pending.filter(from_restaurant=restaurant), many=True, context=ctx).data

    return {
        "venue": VenueCardSerializer(venue, context={"request": request}).data if venue else None,
        "members": VenueMemberSerializer(members, many=True, context={"request": request}).data,
        "shared_tables_count": venue.tables.filter(is_active=True).count() if venue else 0,
        "incoming_requests": incoming,
        "outgoing_requests": outgoing,
        "permissions": {
            "can_manage": staff_can(request, *VENUE_MANAGE),
            "can_leave": staff_can(request, *VENUE_LEAVE) and venue is not None,
        },
    }


class _VenueView(APIView):
    permission_classes = [IsAuthenticated, IsTenantManager, HasStaffPermission]
    required_permission = VENUE_READ

    def _venue(self, request):
        membership = services.get_membership(request.restaurant)
        if membership is None or not membership.venue.is_active:
            raise services.VenueError("This restaurant is not part of a venue.", code="not_a_member", status_code=404)
        return membership.venue

    def _venue_table(self, request, table_id):
        venue = self._venue(request)
        vt = VenueTable.objects.filter(venue=venue, pk=table_id).select_related("venue", "section").first()
        if vt is None:
            raise services.VenueError("Table not found.", code="table_not_found", status_code=404)
        return vt

    def _venue_section(self, request, section_id):
        venue = self._venue(request)
        vs = VenueSection.objects.filter(venue=venue, pk=section_id).select_related("venue").first()
        if vs is None:
            raise services.VenueError("Section not found.", code="section_not_found", status_code=404)
        return vs


@extend_schema(tags=[TAG], responses={200: VenueStateResponseSerializer})
class VenueStateView(_VenueView):
    """Everything the 'Shared venue' page needs, for the current restaurant."""

    @require_restaurant
    def get(self, request):
        return Response({"success": True, "data": build_state(request)})


@extend_schema(tags=[TAG])
class VenueTablesView(_VenueView):
    """The venue's shared tables (registry), with this restaurant's mirror ids."""

    @extend_schema(responses={200: VenueTableDashboardSerializer(many=True)})
    @require_restaurant
    def get(self, request):
        try:
            venue = self._venue(request)
        except services.VenueError as exc:
            return error_response(exc)
        tables = venue.tables.select_related("section").order_by("section__display_order", "number")
        local = {
            t.venue_table_id: t for t in Table.objects.filter(restaurant=request.restaurant, venue_table__in=tables)
        }
        data = VenueTableDashboardSerializer(
            tables, many=True, context={"request": request, "local_tables": local}
        ).data
        return Response({"success": True, "data": data})

    @extend_schema(request=VenueTableWriteSerializer, responses={201: VenueTableDashboardSerializer})
    @require_restaurant
    def post(self, request):
        self.required_permission = VENUE_MANAGE
        self.check_permissions(request)
        serializer = VenueTableWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        fields = dict(serializer.validated_data)
        try:
            venue = self._venue(request)
            fields["section"] = self._venue_section(request, fields.pop("section")) if fields.get("section") else None
            vt, summary = services.create_venue_table(venue, **fields)
        except services.VenueError as exc:
            return error_response(exc)
        return Response(
            {
                "success": True,
                "data": VenueTableDashboardSerializer(vt, context={"request": request}).data,
                "sync": summary.as_dict(),
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=[TAG])
class VenueTableDetailView(_VenueView):
    required_permission = VENUE_MANAGE

    @extend_schema(request=VenueTableWriteSerializer, responses={200: VenueTableDashboardSerializer})
    @require_restaurant
    def patch(self, request, id):
        serializer = VenueTableWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        fields = dict(serializer.validated_data)
        try:
            vt = self._venue_table(request, id)
            if "section" in fields:
                fields["section"] = self._venue_section(request, fields["section"]) if fields["section"] else None
            vt, summary = services.update_venue_table(vt, **fields)
        except services.VenueError as exc:
            return error_response(exc)
        return Response(
            {
                "success": True,
                "data": VenueTableDashboardSerializer(vt, context={"request": request}).data,
                "sync": summary.as_dict(),
            }
        )


@extend_schema(tags=[TAG], request=None, responses={200: None})
class VenueTableDeactivateView(_VenueView):
    required_permission = VENUE_MANAGE

    @require_restaurant
    def post(self, request, id):
        try:
            vt = self._venue_table(request, id)
        except services.VenueError as exc:
            return error_response(exc)
        skipped = services.deactivate_venue_table(vt)
        return Response({"success": True, "data": {"id": str(vt.pk), "is_active": False, "kept_active_at": skipped}})


@extend_schema(tags=[TAG])
class VenueSectionsView(_VenueView):
    @extend_schema(responses={200: VenueSectionDashboardSerializer(many=True)})
    @require_restaurant
    def get(self, request):
        try:
            venue = self._venue(request)
        except services.VenueError as exc:
            return error_response(exc)
        return Response(
            {"success": True, "data": VenueSectionDashboardSerializer(venue.sections.all(), many=True).data}
        )

    @extend_schema(request=VenueSectionWriteSerializer, responses={201: VenueSectionDashboardSerializer})
    @require_restaurant
    def post(self, request):
        self.required_permission = VENUE_MANAGE
        self.check_permissions(request)
        serializer = VenueSectionWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            venue = self._venue(request)
            if venue.sections.filter(name__iexact=serializer.validated_data["name"].strip()).exists():
                raise services.VenueError(
                    "A section with that name already exists.", code="section_exists", field_name="name"
                )
            vs, _ = services.create_venue_section(venue, **serializer.validated_data)
        except services.VenueError as exc:
            return error_response(exc)
        return Response(
            {"success": True, "data": VenueSectionDashboardSerializer(vs).data}, status=status.HTTP_201_CREATED
        )


@extend_schema(tags=[TAG])
class VenueSectionDetailView(_VenueView):
    required_permission = VENUE_MANAGE

    @extend_schema(request=VenueSectionWriteSerializer, responses={200: VenueSectionDashboardSerializer})
    @require_restaurant
    def patch(self, request, id):
        serializer = VenueSectionWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            vs = self._venue_section(request, id)
            vs, _ = services.update_venue_section(vs, **serializer.validated_data)
        except services.VenueError as exc:
            return error_response(exc)
        return Response({"success": True, "data": VenueSectionDashboardSerializer(vs).data})


@extend_schema(tags=[TAG], request=VenueShareRequestCreateSerializer, responses={201: VenueShareRequestSerializer})
class VenueShareRequestCreateView(_VenueView):
    required_permission = VENUE_MANAGE

    @require_restaurant
    def post(self, request):
        serializer = VenueShareRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            req = services.send_share_request(
                request.restaurant,
                serializer.validated_data["to_restaurant"],
                request.user,
                venue_name=serializer.validated_data.get("venue_name", ""),
                message=serializer.validated_data.get("message", ""),
            )
        except services.VenueError as exc:
            return error_response(exc)
        data = VenueShareRequestSerializer(req, context={"restaurant": request.restaurant, "request": request}).data
        return Response({"success": True, "data": data}, status=status.HTTP_201_CREATED)


@extend_schema(tags=[TAG], request=VenueShareRequestAcceptSerializer, responses={200: VenueStateResponseSerializer})
class VenueShareRequestAcceptView(_VenueView):
    required_permission = VENUE_MANAGE

    @require_restaurant
    def post(self, request, id):
        serializer = VenueShareRequestAcceptSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            venue, summary = services.accept_share_request(
                id,
                request.restaurant,
                request.user,
                layout=serializer.validated_data.get("layout"),
                venue_name=serializer.validated_data.get("venue_name", ""),
            )
        except services.VenueError as exc:
            return error_response(exc)
        return Response(
            {"success": True, "data": build_state(request), "sync": SyncSummarySerializer(summary.as_dict()).data}
        )


@extend_schema(tags=[TAG], request=None, responses={200: VenueShareRequestSerializer})
class VenueShareRequestDeclineView(_VenueView):
    required_permission = VENUE_MANAGE

    @require_restaurant
    def post(self, request, id):
        try:
            req = services.decline_share_request(id, request.restaurant, request.user)
        except services.VenueError as exc:
            return error_response(exc)
        return Response(
            {"success": True, "data": VenueShareRequestSerializer(req, context={"restaurant": request.restaurant}).data}
        )


@extend_schema(tags=[TAG], request=None, responses={200: VenueShareRequestSerializer})
class VenueShareRequestCancelView(_VenueView):
    required_permission = VENUE_MANAGE

    @require_restaurant
    def post(self, request, id):
        try:
            req = services.cancel_share_request(id, request.restaurant, request.user)
        except services.VenueError as exc:
            return error_response(exc)
        return Response(
            {"success": True, "data": VenueShareRequestSerializer(req, context={"restaurant": request.restaurant}).data}
        )


@extend_schema(tags=[TAG], request=VenueLeaveSerializer, responses={200: VenueStateResponseSerializer})
class VenueLeaveView(_VenueView):
    required_permission = VENUE_LEAVE

    @require_restaurant
    def post(self, request):
        serializer = VenueLeaveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            services.leave_venue(request.restaurant)
        except services.VenueError as exc:
            return error_response(exc)
        return Response({"success": True, "data": build_state(request)})
