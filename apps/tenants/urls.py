"""
Public restaurant discovery URLs.
"""

from django.urls import path

from apps.menu.views import (
    PublicMenuCategoryListView,
    PublicMenuItemDetailView,
    PublicMenuItemListView,
    PublicMenuView,
)

from .views import (
    RestaurantCitiesView,
    RestaurantCreateView,
    RestaurantDetailView,
    RestaurantHoursView,
    RestaurantListView,
    RestaurantSearchView,
)

app_name = "restaurants"

urlpatterns = [
    path("", RestaurantListView.as_view(), name="list"),
    path("cities/", RestaurantCitiesView.as_view(), name="cities"),
    path("search/", RestaurantSearchView.as_view(), name="search"),
    path("create/", RestaurantCreateView.as_view(), name="create"),
    path("<slug:slug>/", RestaurantDetailView.as_view(), name="detail"),
    path("<slug:slug>/hours/", RestaurantHoursView.as_view(), name="hours"),
    # Menu routes under restaurant
    path("<slug:slug>/menu/", PublicMenuView.as_view(), name="menu"),
    path("<slug:slug>/menu/categories/", PublicMenuCategoryListView.as_view(), name="menu-categories"),
    path("<slug:slug>/menu/items/", PublicMenuItemListView.as_view(), name="menu-items"),
    path("<slug:slug>/menu/items/<uuid:id>/", PublicMenuItemDetailView.as_view(), name="menu-item-detail"),
]
