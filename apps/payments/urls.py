"""
Public payment URLs (customer-facing).
"""

from django.urls import path

from .bog.views import (
    BogStatusView,
    BogWebhookView,
    InitiateAddCardView,
    InitiatePaymentView,
)
from .flitt.views import (
    FlittInitiatePaymentView,
    FlittStatusView,
    FlittWebhookView,
)
from .views import (
    CustomerPaymentHistoryView,
    CustomerPaymentMethodDetailView,
    CustomerPaymentMethodListView,
)

app_name = "payments"

urlpatterns = [
    # Payment methods — add-card is now BOG-backed (redirect to hosted page).
    path("methods/", CustomerPaymentMethodListView.as_view(), name="method-list"),
    path("methods/add/", InitiateAddCardView.as_view(), name="method-add"),
    path("methods/<uuid:id>/", CustomerPaymentMethodDetailView.as_view(), name="method-detail"),
    # Payment history
    path("history/", CustomerPaymentHistoryView.as_view(), name="history"),
    # Bank of Georgia Payment Manager integration
    path("bog/initiate/", InitiatePaymentView.as_view(), name="bog-initiate"),
    path("bog/status/<str:bog_order_id>/", BogStatusView.as_view(), name="bog-status"),
    path("bog/webhook/", BogWebhookView.as_view(), name="bog-webhook"),
    # Flitt (pay.flitt.com) integration — second provider, with native split.
    path("flitt/initiate/", FlittInitiatePaymentView.as_view(), name="flitt-initiate"),
    path(
        "flitt/status/<str:flitt_order_id>/",
        FlittStatusView.as_view(),
        name="flitt-status",
    ),
    path("flitt/webhook/", FlittWebhookView.as_view(), name="flitt-webhook"),
]
