"""
Public URLs for reservations.
"""

from django.urls import path

from . import views

app_name = "reservations"

urlpatterns = [
    # Public endpoints
    path("settings/", views.PublicReservationSettingsView.as_view(), name="settings"),
    path("availability/", views.PublicAvailabilityView.as_view(), name="availability"),
    path("create/", views.PublicReservationCreateView.as_view(), name="create"),
    path("lookup/", views.PublicReservationLookupView.as_view(), name="lookup"),
    path("cancel/", views.PublicReservationCancelView.as_view(), name="cancel"),
    # Customer endpoints (authenticated)
    path("my/", views.CustomerReservationListView.as_view(), name="customer-list"),
    path("my/<uuid:pk>/", views.CustomerReservationDetailView.as_view(), name="customer-detail"),
    path("my/<uuid:pk>/cancel/", views.CustomerReservationCancelView.as_view(), name="customer-cancel"),
]
