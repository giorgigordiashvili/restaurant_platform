from django.urls import path

from .views import FiscalDocumentListView, PaymentReceiptView

app_name = "fiscal_dashboard"

urlpatterns = [
    path("payments/<uuid:id>/receipt/", PaymentReceiptView.as_view(), name="payment-receipt"),
    path("documents/", FiscalDocumentListView.as_view(), name="document-list"),
]
