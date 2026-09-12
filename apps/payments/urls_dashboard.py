"""
Dashboard payment URLs (staff/admin).
"""

from django.urls import path

from .views import (
    CardPaymentCreateView,
    CashPaymentCreateView,
    DiscountReasonListView,
    PaymentDetailView,
    PaymentListView,
    PaymentRefundView,
    PaymentStatsView,
    RecordPaymentView,
    RefundCreateView,
    RefundListView,
    ShiftCloseView,
    ShiftCurrentView,
    ShiftDetailView,
    ShiftListView,
    ShiftMovementsView,
    ShiftOpenView,
    ShiftXReportView,
    SplitEvenView,
)

app_name = "payments_dashboard"

urlpatterns = [
    # Payments
    path("", PaymentListView.as_view(), name="list"),
    path("stats/", PaymentStatsView.as_view(), name="stats"),
    path("record/", RecordPaymentView.as_view(), name="record"),
    path("split-even/", SplitEvenView.as_view(), name="split-even"),
    path("reasons/", DiscountReasonListView.as_view(), name="reasons"),
    # Cash shifts
    path("shifts/", ShiftListView.as_view(), name="shift-list"),
    path("shifts/open/", ShiftOpenView.as_view(), name="shift-open"),
    path("shifts/current/", ShiftCurrentView.as_view(), name="shift-current"),
    path("shifts/current/x-report/", ShiftXReportView.as_view(), name="shift-x-report"),
    path("shifts/<uuid:id>/", ShiftDetailView.as_view(), name="shift-detail"),
    path("shifts/<uuid:id>/close/", ShiftCloseView.as_view(), name="shift-close"),
    path("shifts/<uuid:id>/movements/", ShiftMovementsView.as_view(), name="shift-movements"),
    # Legacy single-order shapes
    path("cash/", CashPaymentCreateView.as_view(), name="cash"),
    path("card/", CardPaymentCreateView.as_view(), name="card"),
    # Refunds
    path("refunds/", RefundListView.as_view(), name="refund-list"),
    path("refunds/create/", RefundCreateView.as_view(), name="refund-create"),
    path("<uuid:id>/", PaymentDetailView.as_view(), name="detail"),
    path("<uuid:id>/refund/", PaymentRefundView.as_view(), name="refund"),
]
