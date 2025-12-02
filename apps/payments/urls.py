"""
Public payment URLs (customer-facing).
"""

from django.urls import path

from .views import (
    CustomerPaymentHistoryView,
    CustomerPaymentMethodCreateView,
    CustomerPaymentMethodDetailView,
    CustomerPaymentMethodListView,
)

app_name = "payments"

urlpatterns = [
    # Payment methods
    path("methods/", CustomerPaymentMethodListView.as_view(), name="method-list"),
    path("methods/add/", CustomerPaymentMethodCreateView.as_view(), name="method-add"),
    path("methods/<uuid:id>/", CustomerPaymentMethodDetailView.as_view(), name="method-detail"),
    # Payment history
    path("history/", CustomerPaymentHistoryView.as_view(), name="history"),
]
