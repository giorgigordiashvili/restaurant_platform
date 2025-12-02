"""
Dashboard URLs for table management.
"""

from django.urls import path

from .views import (
    TableDetailView,
    TableListCreateView,
    TableQRCodeDetailView,
    TableQRCodeListCreateView,
    TableSectionDetailView,
    TableSectionListCreateView,
    TableSessionCloseView,
    TableSessionCreateView,
    TableSessionListView,
    TableStatusUpdateView,
)

app_name = "tables_dashboard"

urlpatterns = [
    # Sections
    path("sections/", TableSectionListCreateView.as_view(), name="section-list"),
    path("sections/<uuid:id>/", TableSectionDetailView.as_view(), name="section-detail"),
    # Tables
    path("", TableListCreateView.as_view(), name="table-list"),
    path("<uuid:id>/", TableDetailView.as_view(), name="table-detail"),
    path("<uuid:id>/status/", TableStatusUpdateView.as_view(), name="table-status"),
    # QR Codes
    path("<uuid:table_id>/qr-codes/", TableQRCodeListCreateView.as_view(), name="qr-list"),
    path("qr-codes/<uuid:id>/", TableQRCodeDetailView.as_view(), name="qr-detail"),
    # Sessions
    path("sessions/", TableSessionListView.as_view(), name="session-list"),
    path("sessions/start/", TableSessionCreateView.as_view(), name="session-start"),
    path("sessions/<uuid:id>/close/", TableSessionCloseView.as_view(), name="session-close"),
]
