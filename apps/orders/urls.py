"""
Public order URLs (customer ordering).
"""

from django.urls import path

from .views import CustomerOrderCreateView, CustomerOrderStatusView

app_name = "orders"

urlpatterns = [
    path("", CustomerOrderCreateView.as_view(), name="create"),
    path("<str:order_number>/status/", CustomerOrderStatusView.as_view(), name="status"),
]
