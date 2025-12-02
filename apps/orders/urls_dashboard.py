"""
Dashboard URLs for order management.
"""

from django.urls import path

from .views import (
    KitchenOrdersView,
    OrderAddItemView,
    OrderCreateView,
    OrderDetailView,
    OrderHistoryView,
    OrderItemStatusUpdateView,
    OrderListView,
    OrderStatusUpdateView,
)

app_name = "orders_dashboard"

urlpatterns = [
    # Orders
    path("", OrderListView.as_view(), name="list"),
    path("create/", OrderCreateView.as_view(), name="create"),
    path("<uuid:id>/", OrderDetailView.as_view(), name="detail"),
    path("<uuid:id>/status/", OrderStatusUpdateView.as_view(), name="status"),
    path("<uuid:id>/items/", OrderAddItemView.as_view(), name="add-item"),
    path("<uuid:order_id>/items/<uuid:item_id>/status/", OrderItemStatusUpdateView.as_view(), name="item-status"),
    path("<uuid:order_id>/history/", OrderHistoryView.as_view(), name="history"),
    # Kitchen display
    path("kitchen/", KitchenOrdersView.as_view(), name="kitchen"),
]
