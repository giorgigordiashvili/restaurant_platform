"""
Views for audit app.
"""

from datetime import timedelta

from django.db.models import Count
from django.utils import timezone

from rest_framework import generics
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.middleware.tenant import require_restaurant
from apps.core.permissions import IsTenantOwner

from .models import AuditLog
from .serializers import (
    AuditLogDetailSerializer,
    AuditLogListSerializer,
    AuditLogStatsSerializer,
)


class DashboardAuditLogListView(generics.ListAPIView):
    """List audit logs for a restaurant (dashboard)."""

    serializer_class = AuditLogListSerializer
    permission_classes = [IsAuthenticated, IsTenantOwner]

    @require_restaurant
    def get_queryset(self):
        queryset = AuditLog.objects.filter(restaurant=self.request.restaurant).select_related("user")

        # Filter by action
        action = self.request.query_params.get("action")
        if action:
            queryset = queryset.filter(action=action)

        # Filter by user
        user_id = self.request.query_params.get("user")
        if user_id:
            queryset = queryset.filter(user_id=user_id)

        # Filter by date range
        start_date = self.request.query_params.get("start_date")
        end_date = self.request.query_params.get("end_date")
        if start_date:
            queryset = queryset.filter(created_at__date__gte=start_date)
        if end_date:
            queryset = queryset.filter(created_at__date__lte=end_date)

        # Filter by target model
        target_model = self.request.query_params.get("target_model")
        if target_model:
            queryset = queryset.filter(target_model=target_model)

        # Search in description
        search = self.request.query_params.get("search")
        if search:
            queryset = queryset.filter(description__icontains=search)

        return queryset.order_by("-created_at")


class DashboardAuditLogDetailView(generics.RetrieveAPIView):
    """Get audit log detail (dashboard)."""

    serializer_class = AuditLogDetailSerializer
    permission_classes = [IsAuthenticated, IsTenantOwner]
    lookup_field = "id"

    @require_restaurant
    def get_queryset(self):
        return AuditLog.objects.filter(restaurant=self.request.restaurant).select_related("user", "restaurant")


class DashboardAuditLogStatsView(APIView):
    """Get audit log statistics for a restaurant (dashboard)."""

    permission_classes = [IsAuthenticated, IsTenantOwner]

    @require_restaurant
    def get(self, request):
        restaurant = request.restaurant
        now = timezone.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        week_start = today_start - timedelta(days=7)

        base_queryset = AuditLog.objects.filter(restaurant=restaurant)

        # Total logs
        total_logs = base_queryset.count()

        # Logs today
        logs_today = base_queryset.filter(created_at__gte=today_start).count()

        # Logs this week
        logs_this_week = base_queryset.filter(created_at__gte=week_start).count()

        # Logs by action (last 30 days)
        thirty_days_ago = now - timedelta(days=30)
        logs_by_action = dict(
            base_queryset.filter(created_at__gte=thirty_days_ago)
            .values("action")
            .annotate(count=Count("id"))
            .values_list("action", "count")
        )

        # Recent logins (last 24 hours)
        yesterday = now - timedelta(days=1)
        recent_logins = base_queryset.filter(action="login", created_at__gte=yesterday).count()

        # Recent failed logins (last 24 hours)
        recent_failed_logins = base_queryset.filter(action="login_failed", created_at__gte=yesterday).count()

        data = {
            "total_logs": total_logs,
            "logs_today": logs_today,
            "logs_this_week": logs_this_week,
            "logs_by_action": logs_by_action,
            "recent_logins": recent_logins,
            "recent_failed_logins": recent_failed_logins,
        }

        serializer = AuditLogStatsSerializer(data)
        return Response(serializer.data)


class DashboardAuditLogActionsView(APIView):
    """Get available audit log actions."""

    permission_classes = [IsAuthenticated, IsTenantOwner]

    def get(self, request):
        actions = [{"value": choice[0], "label": choice[1]} for choice in AuditLog.ACTION_CHOICES]
        return Response(actions)


class DashboardAuditLogExportView(APIView):
    """Export audit logs for a restaurant (dashboard)."""

    permission_classes = [IsAuthenticated, IsTenantOwner]

    @require_restaurant
    def get(self, request):
        from .services import AuditLogService

        # Log the export action
        AuditLogService.log_data_export(
            request=request,
            export_type="audit_logs",
            restaurant=request.restaurant,
            exported_by=request.user,
        )

        # Get logs for export
        queryset = AuditLog.objects.filter(restaurant=request.restaurant).select_related("user")

        # Apply filters
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")
        if start_date:
            queryset = queryset.filter(created_at__date__gte=start_date)
        if end_date:
            queryset = queryset.filter(created_at__date__lte=end_date)

        # Limit to last 10000 records for performance
        queryset = queryset.order_by("-created_at")[:10000]

        # Serialize data
        serializer = AuditLogListSerializer(queryset, many=True)

        return Response(
            {
                "count": len(serializer.data),
                "exported_at": timezone.now().isoformat(),
                "data": serializer.data,
            }
        )


# ============== Admin Views (for platform admins) ==============


class AdminAuditLogListView(generics.ListAPIView):
    """List all audit logs (admin only)."""

    serializer_class = AuditLogListSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        if not self.request.user.is_staff:
            return AuditLog.objects.none()

        queryset = AuditLog.objects.all().select_related("user", "restaurant")

        # Filter by restaurant
        restaurant_id = self.request.query_params.get("restaurant")
        if restaurant_id:
            queryset = queryset.filter(restaurant_id=restaurant_id)

        # Filter by action
        action = self.request.query_params.get("action")
        if action:
            queryset = queryset.filter(action=action)

        # Filter by user
        user_id = self.request.query_params.get("user")
        if user_id:
            queryset = queryset.filter(user_id=user_id)

        return queryset.order_by("-created_at")


class AdminAuditLogDetailView(generics.RetrieveAPIView):
    """Get audit log detail (admin only)."""

    serializer_class = AuditLogDetailSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "id"

    def get_queryset(self):
        if not self.request.user.is_staff:
            return AuditLog.objects.none()
        return AuditLog.objects.all().select_related("user", "restaurant")
