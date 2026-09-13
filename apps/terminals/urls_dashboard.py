from django.urls import path

from .views import (
    PaymentTerminalRefundView,
    SummaryView,
    TerminalListView,
    TransactionCancelView,
    TransactionConfirmView,
    TransactionDeclineView,
    TransactionDetailView,
    TransactionListView,
    TransactionSendLinkView,
)

urlpatterns = [
    path("summary/", SummaryView.as_view(), name="terminals-summary"),
    path("terminals/", TerminalListView.as_view(), name="terminals-list"),
    path("transactions/", TransactionListView.as_view(), name="terminals-transactions"),
    path("transactions/<uuid:tx_id>/", TransactionDetailView.as_view(), name="terminals-transaction"),
    path("transactions/<uuid:tx_id>/confirm/", TransactionConfirmView.as_view(), name="terminals-confirm"),
    path("transactions/<uuid:tx_id>/decline/", TransactionDeclineView.as_view(), name="terminals-decline"),
    path("transactions/<uuid:tx_id>/cancel/", TransactionCancelView.as_view(), name="terminals-cancel"),
    path("transactions/<uuid:tx_id>/send-link/", TransactionSendLinkView.as_view(), name="terminals-send-link"),
    path("payments/<uuid:payment_id>/refund/", PaymentTerminalRefundView.as_view(), name="terminals-refund"),
]
