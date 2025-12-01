"""
Dashboard menu management URLs (tenant-scoped).
"""

from django.urls import path

from .views import (
    MenuCategoryDetailView,
    MenuCategoryListCreateView,
    MenuCategoryReorderView,
    MenuItemDetailView,
    MenuItemImageUploadView,
    MenuItemListCreateView,
    ModifierGroupDetailView,
    ModifierGroupListCreateView,
)

app_name = "menu_dashboard"

urlpatterns = [
    # Categories
    path("categories/", MenuCategoryListCreateView.as_view(), name="category-list"),
    path("categories/<uuid:id>/", MenuCategoryDetailView.as_view(), name="category-detail"),
    path("categories/reorder/", MenuCategoryReorderView.as_view(), name="category-reorder"),

    # Items
    path("items/", MenuItemListCreateView.as_view(), name="item-list"),
    path("items/<uuid:id>/", MenuItemDetailView.as_view(), name="item-detail"),
    path("items/<uuid:id>/image/", MenuItemImageUploadView.as_view(), name="item-image"),

    # Modifier groups
    path("modifier-groups/", ModifierGroupListCreateView.as_view(), name="modifier-group-list"),
    path("modifier-groups/<uuid:id>/", ModifierGroupDetailView.as_view(), name="modifier-group-detail"),
]
