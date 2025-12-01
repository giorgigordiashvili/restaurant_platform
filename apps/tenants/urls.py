"""
Public restaurant discovery URLs.
"""

from django.urls import path

from .views import (
    RestaurantCreateView,
    RestaurantDetailView,
    RestaurantHoursView,
    RestaurantListView,
)

app_name = "restaurants"

urlpatterns = [
    path("", RestaurantListView.as_view(), name="list"),
    path("create/", RestaurantCreateView.as_view(), name="create"),
    path("<slug:slug>/", RestaurantDetailView.as_view(), name="detail"),
    path("<slug:slug>/hours/", RestaurantHoursView.as_view(), name="hours"),
]
