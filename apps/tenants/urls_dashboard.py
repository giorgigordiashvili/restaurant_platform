"""
Restaurant dashboard settings URLs (tenant-scoped).
"""

from django.urls import path

from .views_dashboard import (
    RestaurantCoverUploadView,
    RestaurantHoursUpdateView,
    RestaurantLogoUploadView,
    RestaurantSettingsView,
)

app_name = "dashboard_settings"

urlpatterns = [
    path("", RestaurantSettingsView.as_view(), name="settings"),
    path("hours/", RestaurantHoursUpdateView.as_view(), name="hours"),
    path("logo/", RestaurantLogoUploadView.as_view(), name="logo"),
    path("cover/", RestaurantCoverUploadView.as_view(), name="cover"),
]
