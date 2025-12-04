"""
Public table URLs (QR code scanning and session management).
"""

from django.urls import path

from .views import (
    JoinTableSessionPreviewView,
    JoinTableSessionView,
    LeaveTableSessionView,
    QRCodeScanView,
    TableSessionDetailPublicView,
    TableSessionGuestsView,
    TableSessionInviteView,
    TableSessionOrdersView,
    TableValidateView,
)

app_name = "tables"

urlpatterns = [
    # QR Code scanning
    path("scan/", QRCodeScanView.as_view(), name="qr-scan"),
    # Table validation (for QR code URL parameter)
    path("validate/<str:code>/", TableValidateView.as_view(), name="table-validate"),
    # Session join flow
    path(
        "sessions/join/<str:invite_code>/",
        JoinTableSessionPreviewView.as_view(),
        name="session-join-preview",
    ),
    path(
        "sessions/join/<str:invite_code>/confirm/",
        JoinTableSessionView.as_view(),
        name="session-join",
    ),
    # Session details and management
    path(
        "sessions/<uuid:id>/",
        TableSessionDetailPublicView.as_view(),
        name="session-detail",
    ),
    path(
        "sessions/<uuid:session_id>/invite/",
        TableSessionInviteView.as_view(),
        name="session-invite",
    ),
    path(
        "sessions/<uuid:session_id>/guests/",
        TableSessionGuestsView.as_view(),
        name="session-guests",
    ),
    path(
        "sessions/<uuid:session_id>/orders/",
        TableSessionOrdersView.as_view(),
        name="session-orders",
    ),
    path(
        "sessions/<uuid:session_id>/leave/",
        LeaveTableSessionView.as_view(),
        name="session-leave",
    ),
]
