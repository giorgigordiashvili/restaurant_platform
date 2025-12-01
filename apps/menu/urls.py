"""
Public menu URLs.
"""

from django.urls import path

from .views import PublicMenuItemDetailView, PublicMenuView

app_name = "menu"

urlpatterns = [
    path("<slug:slug>/", PublicMenuView.as_view(), name="restaurant-menu"),
    path("<slug:slug>/items/<uuid:id>/", PublicMenuItemDetailView.as_view(), name="item-detail"),
]
