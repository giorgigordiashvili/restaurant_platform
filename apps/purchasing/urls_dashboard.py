from django.urls import path

from .views import (
    FromBuyListView,
    PurchaseOrderDetailView,
    PurchaseOrderListView,
    PurchaseOrderReceiveView,
    SupplierListView,
)

urlpatterns = [
    path("suppliers/", SupplierListView.as_view(), name="purchasing-suppliers"),
    path("orders/", PurchaseOrderListView.as_view(), name="purchasing-orders"),
    path("orders/from-buy-list/", FromBuyListView.as_view(), name="purchasing-from-buy-list"),
    path("orders/<uuid:order_id>/", PurchaseOrderDetailView.as_view(), name="purchasing-order"),
    path("orders/<uuid:order_id>/receive/", PurchaseOrderReceiveView.as_view(), name="purchasing-receive"),
]
