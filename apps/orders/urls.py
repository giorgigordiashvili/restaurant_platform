"""
Public order URLs (customer ordering).
"""

from django.urls import path

from .views import (
    CustomerMyOrderDetailView,
    CustomerMyOrdersListView,
    CustomerOrderCreateView,
    CustomerOrderStatusView,
)

app_name = "orders"

urlpatterns = [
    path("create/", CustomerOrderCreateView.as_view(), name="create"),
    # Authenticated customer endpoints — placed before the public lookup so
    # `/my/` doesn't get matched as an order_number.
    path("my/", CustomerMyOrdersListView.as_view(), name="my-list"),
    path("my/<str:order_number>/", CustomerMyOrderDetailView.as_view(), name="my-detail"),
    path("<str:order_number>/", CustomerOrderStatusView.as_view(), name="status"),
]
