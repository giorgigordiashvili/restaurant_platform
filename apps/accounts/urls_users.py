"""
URL patterns for user profile endpoints.
"""

from django.urls import path

from .views import CurrentUserView, DeleteAccountView, MyRestaurantsView

app_name = "users"

urlpatterns = [
    # Current user profile
    path("me/", CurrentUserView.as_view(), name="current_user"),
    path("me/delete/", DeleteAccountView.as_view(), name="delete_account"),
    path("me/restaurants/", MyRestaurantsView.as_view(), name="my_restaurants"),
]
