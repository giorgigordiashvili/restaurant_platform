"""Dashboard API for the reports: the same numbers the admin pages show, for the POS later."""

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.utils import OpenApiParameter, extend_schema

from apps.core import modules
from apps.core.middleware.tenant import require_restaurant
from apps.core.permissions import HasStaffPermission, IsTenantStaff
from apps.reports.periods import RANGE_KEYS, parse_period
from apps.reports.tenant_admin import ADMINS, report_data, report_module

PARAMS = [
    OpenApiParameter("range", str, description="|".join(RANGE_KEYS)),
    OpenApiParameter("from", str, description="YYYY-MM-DD (custom)"),
    OpenApiParameter("to", str, description="YYYY-MM-DD (custom)"),
]


@extend_schema(tags=["Dashboard - Reports"], parameters=PARAMS)
class ReportView(APIView):
    permission_classes = [IsAuthenticated, IsTenantStaff, HasStaffPermission]
    required_permission = ("analytics", "read")

    @require_restaurant
    def get(self, request, key):
        if key not in ADMINS:
            return Response(
                {"success": False, "error": {"message": "Unknown report."}}, status=status.HTTP_404_NOT_FOUND
            )
        module = report_module(key)
        if module and not modules.is_enabled(request.restaurant, module):
            return Response(
                {"success": False, "error": {"code": "module_disabled", "module": module}},
                status=status.HTTP_404_NOT_FOUND,
            )
        period = parse_period(request.query_params, request.restaurant)
        data = report_data(request, key, period)
        return Response(
            {
                "success": True,
                "data": {
                    "report": key,
                    "period": {
                        "key": period.key,
                        "from": period.start_date,
                        "to": period.end_date,
                        "tz": str(period.tz),
                    },
                    **data,
                },
            }
        )
