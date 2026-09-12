"""
Dashboard URLs for order management.
"""

from django.urls import path

from .views import (
    KitchenOrdersView,
    OrderAddItemView,
    OrderCreateView,
    OrderDetailView,
    OrderDiscountView,
    OrderHistoryView,
    OrderItemCompView,
    OrderItemDiscountView,
    OrderItemStatusUpdateView,
    OrderItemVoidView,
    OrderListView,
    OrderMoveView,
    OrderServerAssignView,
    OrderSplitView,
    OrderStatusUpdateView,
    TipReportView,
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
    # Money: discounts / comps / voids / split / move
    path("<uuid:id>/discount/", OrderDiscountView.as_view(), name="discount"),
    path("<uuid:id>/split/", OrderSplitView.as_view(), name="split"),
    path("<uuid:id>/move/", OrderMoveView.as_view(), name="move"),
    path("<uuid:order_id>/items/<uuid:item_id>/void/", OrderItemVoidView.as_view(), name="item-void"),
    path("<uuid:order_id>/items/<uuid:item_id>/comp/", OrderItemCompView.as_view(), name="item-comp"),
    path("<uuid:order_id>/items/<uuid:item_id>/discount/", OrderItemDiscountView.as_view(), name="item-discount"),
    # Kitchen display
    path("kitchen/", KitchenOrdersView.as_view(), name="kitchen"),
    # Tips
    path("<uuid:id>/server/", OrderServerAssignView.as_view(), name="server-assign"),
    path("tips/report/", TipReportView.as_view(), name="tips-report"),
]
