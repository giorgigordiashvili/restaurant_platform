"""
Dashboard payment URLs (staff/admin).
"""

from django.urls import path

from .views import (
    CardPaymentCreateView,
    CashPaymentCreateView,
    PaymentDetailView,
    PaymentListView,
    PaymentStatsView,
    RefundCreateView,
    RefundListView,
)

app_name = "payments_dashboard"

urlpatterns = [
    # Payments
    path("", PaymentListView.as_view(), name="list"),
    path("stats/", PaymentStatsView.as_view(), name="stats"),
    path("<uuid:id>/", PaymentDetailView.as_view(), name="detail"),
    # Payment processing
    path("cash/", CashPaymentCreateView.as_view(), name="cash"),
    path("card/", CardPaymentCreateView.as_view(), name="card"),
    # Refunds
    path("refunds/", RefundListView.as_view(), name="refund-list"),
    path("refunds/create/", RefundCreateView.as_view(), name="refund-create"),
]
