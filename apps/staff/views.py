"""
Staff management views for restaurant dashboard.
"""

from django.conf import settings

from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.utils import extend_schema

from apps.core.middleware.tenant import require_restaurant
from apps.core.permissions import IsTenantManager

from . import emails, services
from .models import StaffInvitation, StaffMember, StaffRole
from .serializers import (
    AcceptInvitationSerializer,
    StaffInvitationSerializer,
    StaffInviteSerializer,
    StaffMemberDetailSerializer,
    StaffMemberSerializer,
    StaffMemberUpdateSerializer,
    StaffRoleListSerializer,
    StaffRoleSerializer,
)


@extend_schema(tags=["Dashboard - Staff"])
class StaffListView(generics.ListAPIView):
    """List all staff members for a restaurant."""

    serializer_class = StaffMemberSerializer
    permission_classes = [IsAuthenticated, IsTenantManager]
    required_permission = ("staff", "read")

    @require_restaurant
    def get_queryset(self):
        return StaffMember.objects.filter(restaurant=self.request.restaurant).select_related("user", "role")


@extend_schema(tags=["Dashboard - Staff"])
class StaffDetailView(generics.RetrieveUpdateDestroyAPIView):
    """Get, update, or remove a staff member."""

    permission_classes = [IsAuthenticated, IsTenantManager]
    required_permission = ("staff", "update")
    lookup_field = "id"

    def get_serializer_class(self):
        if self.request.method in ("PUT", "PATCH"):
            return StaffMemberUpdateSerializer
        return StaffMemberDetailSerializer

    @require_restaurant
    def get_queryset(self):
        return StaffMember.objects.filter(restaurant=self.request.restaurant).select_related(
            "user", "role", "invited_by"
        )

    def perform_destroy(self, instance):
        """Deactivate instead of delete."""
        # Prevent removing yourself
        if instance.user == self.request.user:
            return Response(
                {"error": "You cannot remove yourself from staff."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Prevent removing the owner
        if instance.restaurant.owner == instance.user:
            return Response(
                {"error": "You cannot remove the restaurant owner."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        instance.deactivate()

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()

        # Prevent removing yourself
        if instance.user == request.user:
            return Response(
                {"success": False, "error": {"message": "You cannot remove yourself from staff."}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Prevent removing the owner
        if instance.restaurant.owner == instance.user:
            return Response(
                {"success": False, "error": {"message": "You cannot remove the restaurant owner."}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        instance.deactivate()
        return Response(
            {"success": True, "message": "Staff member removed."},
            status=status.HTTP_200_OK,
        )


@extend_schema(tags=["Dashboard - Staff"])
class StaffInviteView(APIView):
    """Invite a new staff member via email."""

    permission_classes = [IsAuthenticated, IsTenantManager]
    required_permission = ("staff", "create")

    @require_restaurant
    def post(self, request):
        serializer = StaffInviteSerializer(
            data=request.data,
            context={
                "request": request,
                "restaurant": request.restaurant,
            },
        )
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            invitation = services.invite(
                request.restaurant,
                data["email"],
                data["role_id"],
                invited_by=request.user,
                message=data.get("message", ""),
            )
        except services.InviteError as exc:
            return Response({"success": False, "error": {"message": str(exc)}}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "success": True,
                "message": f"Invitation sent to {invitation.email}",
                "data": StaffInvitationSerializer(invitation).data,
            },
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Dashboard - Staff"])
class StaffInvitationsListView(generics.ListAPIView):
    """List pending invitations for a restaurant."""

    serializer_class = StaffInvitationSerializer
    permission_classes = [IsAuthenticated, IsTenantManager]
    required_permission = ("staff", "read")

    @require_restaurant
    def get_queryset(self):
        return StaffInvitation.objects.filter(
            restaurant=self.request.restaurant,
            status="pending",
        ).select_related("role", "invited_by")


@extend_schema(tags=["Dashboard - Staff"])
class StaffInvitationCancelView(APIView):
    """Cancel a pending invitation."""

    permission_classes = [IsAuthenticated, IsTenantManager]
    required_permission = ("staff", "delete")

    @require_restaurant
    def post(self, request, id):
        try:
            invitation = StaffInvitation.objects.get(
                id=id,
                restaurant=request.restaurant,
                status="pending",
            )
            invitation.cancel()
            return Response(
                {"success": True, "message": "Invitation cancelled."},
                status=status.HTTP_200_OK,
            )
        except StaffInvitation.DoesNotExist:
            return Response(
                {"success": False, "error": {"message": "Invitation not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )


@extend_schema(tags=["Staff"])
class AcceptInvitationView(APIView):
    """Accept a staff invitation (public endpoint)."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = AcceptInvitationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        invitation = serializer.validated_data["token"]
        staff_member = invitation.accept(request.user)

        return Response(
            {
                "success": True,
                "message": f"You have joined {invitation.restaurant.name} as {staff_member.role.get_display_name()}.",
                "data": {
                    **StaffMemberDetailSerializer(staff_member).data,
                    "restaurant_name": invitation.restaurant.name,
                    "restaurant_slug": invitation.restaurant.slug,
                    "admin_url": emails.admin_url(invitation.restaurant),
                    "pos_url": settings.POS_BASE_URL,
                },
            },
            status=status.HTTP_200_OK,
        )


@extend_schema(tags=["Staff"])
class InvitationDetailsView(APIView):
    """Get invitation details by token (public endpoint for invite page)."""

    permission_classes = [AllowAny]

    def get(self, request, token):
        try:
            invitation = StaffInvitation.objects.select_related("restaurant", "role").get(token=token)

            return Response(
                {
                    "success": True,
                    "data": {
                        "restaurant_name": invitation.restaurant.name,
                        "restaurant_slug": invitation.restaurant.slug,
                        "restaurant_logo": (
                            request.build_absolute_uri(invitation.restaurant.logo.url)
                            if invitation.restaurant.logo
                            else None
                        ),
                        "role": invitation.role.get_display_name(),
                        "invited_by": invitation.invited_by.full_name if invitation.invited_by_id else "",
                        "is_valid": invitation.is_valid,
                        "expires_at": invitation.expires_at,
                        "status": (
                            "expired" if invitation.status == "pending" and invitation.is_expired else invitation.status
                        ),
                        "admin_url": emails.admin_url(invitation.restaurant),
                        "pos_url": settings.POS_BASE_URL,
                    },
                },
                status=status.HTTP_200_OK,
            )
        except StaffInvitation.DoesNotExist:
            return Response(
                {"success": False, "error": {"message": "Invitation not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )


@extend_schema(tags=["Dashboard - Staff"])
class StaffRolesListView(generics.ListAPIView):
    """List available staff roles for a restaurant."""

    serializer_class = StaffRoleListSerializer
    permission_classes = [IsAuthenticated, IsTenantManager]

    @require_restaurant
    def get_queryset(self):
        return StaffRole.objects.filter(restaurant=self.request.restaurant)


@extend_schema(tags=["Dashboard - Staff"])
class StaffRoleDetailView(generics.RetrieveUpdateAPIView):
    """Get or update a staff role."""

    serializer_class = StaffRoleSerializer
    permission_classes = [IsAuthenticated, IsTenantManager]
    required_permission = ("staff", "update")
    lookup_field = "id"

    @require_restaurant
    def get_queryset(self):
        return StaffRole.objects.filter(restaurant=self.request.restaurant)

    def perform_update(self, serializer):
        instance = serializer.instance
        if instance.is_system_role and "permissions" in serializer.validated_data:
            # Allow updating permissions on system roles, but not name
            pass
        serializer.save()
