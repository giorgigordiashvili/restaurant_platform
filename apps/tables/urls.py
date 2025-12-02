"""
Public table URLs (QR code scanning).
"""

from django.urls import path

from .views import QRCodeScanView

app_name = "tables"

urlpatterns = [
    path("scan/", QRCodeScanView.as_view(), name="qr-scan"),
]
