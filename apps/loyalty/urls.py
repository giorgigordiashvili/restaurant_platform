from django.urls import path

from . import views

app_name = "loyalty"

urlpatterns = [
    path("my/", views.CustomerLoyaltyListView.as_view(), name="customer-list"),
    path("my/redeem/", views.CustomerLoyaltyRedeemView.as_view(), name="customer-redeem"),
    path(
        "platform/status/",
        views.PlatformLoyaltyStatusView.as_view(),
        name="platform-status",
    ),
]
