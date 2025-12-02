"""
URL configuration for favorites app.
"""

from django.urls import path

from . import views

app_name = "favorites"

urlpatterns = [
    # Combined
    path("counts/", views.FavoriteCountsView.as_view(), name="counts"),
    path("clear/", views.ClearAllFavoritesView.as_view(), name="clear-all"),
    # Restaurants
    path(
        "restaurants/",
        views.FavoriteRestaurantListView.as_view(),
        name="restaurant-list",
    ),
    path(
        "restaurants/add/",
        views.FavoriteRestaurantCreateView.as_view(),
        name="restaurant-add",
    ),
    path(
        "restaurants/<uuid:restaurant_id>/remove/",
        views.FavoriteRestaurantDeleteView.as_view(),
        name="restaurant-remove",
    ),
    path(
        "restaurants/<uuid:restaurant_id>/toggle/",
        views.FavoriteRestaurantToggleView.as_view(),
        name="restaurant-toggle",
    ),
    path(
        "restaurants/<uuid:restaurant_id>/status/",
        views.FavoriteRestaurantStatusView.as_view(),
        name="restaurant-status",
    ),
    path(
        "restaurants/bulk-status/",
        views.BulkFavoriteRestaurantStatusView.as_view(),
        name="restaurant-bulk-status",
    ),
    # Menu Items
    path(
        "menu-items/",
        views.FavoriteMenuItemListView.as_view(),
        name="menu-item-list",
    ),
    path(
        "menu-items/add/",
        views.FavoriteMenuItemCreateView.as_view(),
        name="menu-item-add",
    ),
    path(
        "menu-items/<uuid:menu_item_id>/remove/",
        views.FavoriteMenuItemDeleteView.as_view(),
        name="menu-item-remove",
    ),
    path(
        "menu-items/<uuid:menu_item_id>/toggle/",
        views.FavoriteMenuItemToggleView.as_view(),
        name="menu-item-toggle",
    ),
    path(
        "menu-items/<uuid:menu_item_id>/status/",
        views.FavoriteMenuItemStatusView.as_view(),
        name="menu-item-status",
    ),
    path(
        "menu-items/bulk-status/",
        views.BulkFavoriteMenuItemStatusView.as_view(),
        name="menu-item-bulk-status",
    ),
]
